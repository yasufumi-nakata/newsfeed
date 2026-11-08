"""Quantity recording, normal-to-bonus transitions and status-only migration."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from quest_store import QuestStore, QuestConflict
from test_quests import Clock


class QuantityTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = QuestStore(":memory:", self.clock)
        self.addCleanup(self.store.close)

    def save(self, quest, amount, **kwargs):
        return self.store.save_quest(quest, self.store.today(), amount=amount, **kwargs)

    def quest(self, quest):
        return next(q for q in self.store.snapshot()["quests"] if q["id"] == quest)

    def clear_normals(self, except_id=None):
        for q in self.store.snapshot()["quests"]:
            if q["id"] != except_id:
                self.save(q["id"], max(q["amount"], q["normal_target"]))
        return self.store.snapshot()

    def test_one_repetition_is_progress_and_only_threshold_awards_xp(self):
        for amount in range(1, 6):
            p = self.save("pushups", amount)
            self.assertEqual(self.quest("pushups")["amount"], amount)
            self.assertEqual(p["quest_xp"], 10 if amount == 5 else 0)
        p = self.save("pushups", 10)
        self.assertEqual(p["quest_xp"], 10)
        self.assertFalse(p["bonus_unlocked"])
        self.assertEqual(self.quest("pushups")["status"], "normal")

    def test_decimal_distance_and_integer_units(self):
        self.assertEqual(self.save("walk", 2.49)["quest_xp"], 0)
        self.assertEqual(self.save("walk", 2.5)["quest_xp"], 10)
        for quest in ("pushups", "research", "writing"):
            with self.assertRaises(ValueError):
                self.save(quest, 1.5)
        self.assertEqual(self.quest("walk")["amount"], 2.5)

    def test_bad_measurements_never_change_records(self):
        self.save("walk", 1)
        for value in (-1, True, "1", [], {}, float("nan"), float("inf"), 1000001):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.save("walk", value)
        self.assertEqual(self.quest("walk")["amount"], 1)

    def test_bonus_appears_only_after_all_eleven_normals(self):
        p = self.clear_normals(except_id="math")
        self.assertEqual(p["quest_done"], 10)
        self.assertEqual(p["stage"], "normal")
        self.assertTrue(all(q["target"] == q["normal_target"] for q in p["quests"]))
        p = self.save("math", 10)
        self.assertEqual((p["stage"], p["quest_done"], p["quest_xp"], p["bonus_done"]), ("bonus", 11, 110, 0))
        self.assertTrue(all(q["target"] == q["bonus_target"] for q in p["quests"]))

    def test_excess_quantity_carries_into_bonus_and_corrections_relock(self):
        self.save("walk", 5, safe_bonus=True)
        self.clear_normals()
        q = self.quest("walk")
        self.assertEqual((q["amount"], q["status"], q["xp"], q["gp"], q["bonus_days"]), (5, "bonus", 20, 2, 1))
        p = self.save("pushups", 1)
        self.assertFalse(p["bonus_unlocked"])
        self.assertEqual(p["quest_xp"], 100)
        q = self.quest("walk")
        self.assertEqual((q["amount"], q["status"], q["xp"], q["gp"], q["bonus_days"]), (5, "normal", 10, 1, 0))
        self.save("pushups", 5)
        self.assertEqual(self.store.snapshot()["quest_xp"], 120)

    def test_repeated_save_and_reunlock_never_duplicate_rewards(self):
        self.clear_normals()
        self.save("pushups", 10)
        self.save("pushups", 10)
        self.assertEqual(self.store.snapshot()["quest_xp"], 120)
        for _ in range(3):
            self.save("math", 0)
            self.save("math", 10)
        self.assertEqual(self.store.snapshot()["quest_xp"], 120)

    def test_rest_keeps_amount_but_does_not_unlock_bonus(self):
        self.clear_normals()
        p = self.store.save_quest("pushups", self.store.today(), rest=True)
        self.assertEqual((p["stage"], p["quest_done"], p["quest_rest"], p["quest_xp"]), ("normal", 10, 1, 100))
        self.assertEqual(self.quest("pushups")["amount"], 5)
        p = self.store.save_quest("pushups", self.store.today(), rest=False)
        self.assertTrue(p["bonus_unlocked"])
        self.assertEqual(p["quest_xp"], 110)

    def test_todoist_does_not_unlock_quest_stage(self):
        for i in range(20):
            self.store.record_local_completion(str(i), "task")
        p = self.store.snapshot()
        self.assertEqual(p["total_xp"], 200)
        self.assertEqual((p["stage"], p["quest_done"]), ("normal", 0))

    def test_new_day_returns_to_normal_with_zero_amounts(self):
        self.clear_normals()
        before = self.store.today()
        self.clock.advance()
        p = self.store.snapshot()
        self.assertEqual(p["stage"], "normal")
        self.assertTrue(all(q["amount"] == 0 for q in p["quests"]))
        self.assertEqual((p["today_xp"], p["total_xp"]), (0, 110))
        with self.assertRaises(ValueError):
            self.store.save_quest("walk", before, amount=5)

    def test_flags_preserved_when_only_amount_is_saved(self):
        self.save("walk", 1, safe_bonus=True, hold=True)
        self.save("walk", 2)
        self.assertTrue(self.quest("walk")["safe_bonus"])
        self.assertTrue(self.quest("walk")["hold"])

    def test_concurrent_edit_does_not_overwrite_newer_measurement(self):
        self.save("pushups", 1, expected_amount=0)
        with self.assertRaises(QuestConflict):
            self.save("pushups", 2, expected_amount=0)
        self.assertEqual(self.quest("pushups")["amount"], 1)
        self.save("pushups", 2, expected_amount=1)
        self.assertEqual(self.quest("pushups")["amount"], 2)

    def test_legacy_migration_keeps_history_and_restart_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.sqlite3"
            today = self.store.today()
            yesterday = (self.clock().date() - timedelta(days=1)).isoformat()
            with closing(sqlite3.connect(path)) as old, old:
                old.executescript("""CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE quest_records (day TEXT, quest_id TEXT, status TEXT, safe_bonus INTEGER, hold INTEGER, PRIMARY KEY(day,quest_id));""")
                old.execute("INSERT INTO metadata VALUES ('started_on', ?)", (yesterday,))
                old.executemany("INSERT INTO quest_records VALUES (?,?,?,?,?)", [(yesterday,"research","bonus",0,0),(today,"walk","normal",1,1)])
            for _ in range(2):
                migrated = QuestStore(path, self.clock)
                try:
                    p = migrated.snapshot()
                    self.assertEqual((p["total_xp"], p["quest_xp"], p["day_number"]), (30, 10, 2))
                    self.assertEqual(p["quests"][0]["amount"], 2.5)
                    self.assertTrue(p["quests"][0]["hold"])
                finally:
                    migrated.close()


if __name__ == "__main__":
    unittest.main()
