"""Rules, persistence and integration regressions; no real tasks are changed."""
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import rss_signage
import todoist_api
from quest_store import QUESTS, QuestStore, utc_iso
from test_task_completion import task


class Clock:
    def __init__(self):
        self.value = datetime(2026, 11, 8, 3, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, days=1):
        self.value += timedelta(days=days)


def activity(event_id, task_id, when, kind="completed", actor="me"):
    return {"id": event_id, "object_type": "item", "object_id": task_id,
            "event_type": kind, "event_date": utc_iso(when), "initiator_id": actor,
            "extra_data": {"content": "テストタスク"}}


class QuestRulesTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = QuestStore(":memory:", self.clock)
        self.addCleanup(self.store.close)

    def record(self, quest="research", status="bonus", **kwargs):
        # Growth tests isolate one quest; the rest of that day's normal stage
        # must also be complete before a bonus can count under the new rules.
        if status == "bonus":
            for q in self.store.snapshot()["quests"]:
                if q["id"] != quest and not q["normal_complete"]:
                    self.store.save_quest(q["id"], self.store.today(), "normal")
        return self.store.save_quest(quest, self.store.today(), status, **kwargs)

    def quest(self, quest="research"):
        return next(q for q in self.store.snapshot()["quests"] if q["id"] == quest)

    def test_exact_initial_targets_from_document(self):
        self.assertEqual([(q["normal_target"], q["bonus_target"]) for q in self.store.snapshot()["quests"]],
                         [(2.5,5),(5,10),(10,20),(15,30),(5,10),(45,90),(15,30),(15,30),(10,20),(50,100),(10,20)])

    def test_all_normal_and_bonus_awards_and_overall_level(self):
        for q in QUESTS:
            self.record(q[0], "normal")
        self.assertEqual(self.store.snapshot()["today_xp"], 110)
        for q in QUESTS:
            self.record(q[0], "bonus")
        result = self.store.snapshot()
        self.assertEqual((result["today_xp"],result["total_xp"],result["level"],result["level_progress"]), (220,220,3,20))

    def test_same_day_corrections_are_differences_not_repeated_awards(self):
        for status, expected in (("normal",10),("normal",10),("bonus",20),("bonus",20),("rest",0),("missed",0)):
            with self.subTest(status=status):
                self.record(status=status)
                self.assertEqual(self.quest()["xp"], expected)
        self.assertEqual(self.quest()["gp"], 0)

    def test_first_fourteen_days_hold_growth_and_day_fifteen_schedules_tomorrow(self):
        for day in range(14):
            self.record()
            self.assertFalse(self.quest()["promotion_pending"])
            self.clock.advance()
        self.record()
        q = self.quest()
        self.assertEqual((q["level"],q["bonus_target"],q["next_level"],q["gp"],q["bonus_days"]), (1,90,2,10,0))
        self.assertTrue(q["promotion_pending"])
        self.clock.advance()
        q = self.quest()
        self.assertEqual((q["level"],q["normal_target"],q["bonus_target"],q["status"]), (2,50,100,"missed"))
        self.assertEqual(self.store.snapshot()["today_xp"], 0)

    def test_normals_alone_never_promote_and_nonconsecutive_bonuses_count(self):
        for day in range(23):
            self.record(status="normal")
            self.clock.advance()
        self.assertEqual(self.quest()["level"],1)
        for day in range(3):
            self.record()
            if day < 2:
                self.clock.advance(2)
        self.assertTrue(self.quest()["promotion_pending"])
        self.assertEqual(self.quest()["gp"],9)

    def test_same_day_correction_withdraws_pending_promotion(self):
        for day in range(18):
            self.record(status="bonus" if day < 2 else "normal")
            self.clock.advance()
        self.record()
        self.assertTrue(self.quest()["promotion_pending"])
        self.record(status="normal")
        self.assertFalse(self.quest()["promotion_pending"])
        self.assertEqual(self.quest()["next_level"],1)

    def test_physical_safety_confirmation_and_growth_hold(self):
        for day in range(15):
            self.record("walk")
            self.clock.advance()
        self.assertEqual(self.quest("walk")["bonus_days"],0)
        for day in range(3):
            self.record("walk", safe_bonus=True, hold=True)
            if day < 2:
                self.clock.advance()
        self.assertFalse(self.quest("walk")["promotion_pending"])
        self.record("walk", safe_bonus=True, hold=False)
        q = self.quest("walk")
        self.assertTrue(q["promotion_pending"])
        self.clock.advance()
        self.assertEqual(self.quest("walk")["normal_target"],3)

    def test_cap_rounding_and_one_promotion_per_day(self):
        for day in range(100):
            self.record("listening")
            self.record("listening")
            q = self.quest("listening")
            self.assertLessEqual(q["next_level"] - q["level"],1)
            self.clock.advance()
        q = self.quest("listening")
        self.assertEqual((q["level"],q["normal_target"],q["bonus_target"],q["at_cap"]),(9,30,60,True))
        self.record("listening")
        self.assertEqual(self.quest("listening")["xp"],20)

    def test_jst_midnight_rollover_rejects_stale_day(self):
        self.clock.value = datetime(2026,11,8,14,59,tzinfo=timezone.utc)
        old = self.store.today()
        self.record(status="normal")
        self.clock.value += timedelta(minutes=1)
        self.assertEqual(self.store.today(),"2026-11-09")
        self.assertEqual(self.store.snapshot()["today_xp"],0)
        self.assertEqual(self.store.snapshot()["total_xp"],10)
        with self.assertRaises(ValueError):
            self.store.save_quest("research",old,"bonus")

    def test_invalid_status_quest_or_boolean_does_not_change_xp(self):
        for args in (("unknown",self.store.today(),"normal"),("walk",self.store.today(),[]),("walk",self.store.today(),"bonus","true")):
            with self.assertRaises(ValueError):
                self.store.save_quest(*args)
        self.assertEqual(self.store.snapshot()["total_xp"],0)

    def test_restart_retains_progress_and_start_date(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quests.sqlite3"
            first = QuestStore(path,self.clock)
            first.save_quest("walk",first.today(),"bonus",True)
            first.record_local_completion("a","task")
            first.close()
            self.clock.advance()
            second = QuestStore(path,self.clock)
            self.addCleanup(second.close)
            self.assertEqual(second.snapshot()["total_xp"],20)
            self.assertEqual(second.snapshot()["day_number"],2)
            self.assertEqual(second.snapshot()["today_xp"],0)


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = QuestStore(":memory:",self.clock)
        self.addCleanup(self.store.close)

    def ingest(self,*events):
        self.store.ingest_activity(events,utc_iso(self.clock()))

    def test_local_and_remote_completion_only_count_once_after_repeated_sync(self):
        self.store.record_local_completion("task","title")
        event = activity(1,"task",self.clock())
        self.ingest(event,event)
        self.ingest(event)
        self.assertEqual(self.store.snapshot()["total_xp"],10)

    def test_same_task_next_day_is_rewarded_and_reopen_removes_latest_only(self):
        self.ingest(activity(1,"task",self.clock()))
        self.clock.advance()
        self.ingest(activity(2,"task",self.clock()))
        self.assertEqual(self.store.snapshot()["total_xp"],20)
        self.clock.value += timedelta(minutes=1)
        self.ingest(activity(3,"task",self.clock(),"uncompleted"))
        self.assertEqual(self.store.snapshot()["total_xp"],10)
        self.assertEqual(self.store.snapshot()["today_xp"],0)
        self.clock.value += timedelta(minutes=1)
        self.ingest(activity(4,"task",self.clock()))
        self.assertEqual(self.store.snapshot()["total_xp"],20)

    def test_reopen_on_next_day_removes_prior_award(self):
        self.ingest(activity(1,"task",self.clock()))
        self.clock.advance()
        self.ingest(activity(2,"task",self.clock(),"uncompleted"))
        self.assertEqual(self.store.snapshot()["total_xp"],0)

    def test_task_deleted_without_completion_does_not_award_xp(self):
        self.ingest(activity(1,"task",self.clock(),"deleted"))
        self.assertEqual(self.store.snapshot()["total_xp"],0)

    def test_events_before_installation_are_excluded(self):
        self.ingest(activity(1,"old",self.clock()-timedelta(days=1)),activity(2,"today",self.clock()))
        self.assertEqual(self.store.snapshot()["total_xp"],10)

    def test_malformed_batch_does_not_advance_cursor_or_partially_commit(self):
        invalid = activity(2,"task",self.clock())
        invalid["event_date"] = "invalid"
        with self.assertRaises(ValueError):
            self.ingest(activity(1,"task",self.clock()),invalid)
        self.assertIsNone(self.store.snapshot()["todoist_synced_at"])
        self.assertEqual(self.store.snapshot()["total_xp"],0)

    @patch("todoist_api._request_json")
    def test_activity_pagination_and_collaborator_filter(self, request):
        request.side_effect = [{"results":[activity(1,"a",self.clock())],"next_cursor":"next"},
                               {"results":[activity(2,"b",self.clock(),actor="other")],"next_cursor":None}]
        result = todoist_api.collect_activity("token","me","start","end")
        self.assertEqual([e["id"] for e in result],[1])
        self.assertEqual(request.call_count,2)

    @patch("todoist_api._request_json")
    def test_bad_cursor_fails_instead_of_looping(self, request):
        request.return_value = {"results":[],"next_cursor":"same"}
        with self.assertRaises(todoist_api.TodoistError):
            todoist_api.collect_activity("token","me","start","end")
        self.assertEqual(request.call_count,2)


class DashboardHandlerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = QuestStore(":memory:", self.clock)
        self.addCleanup(self.store.close)
        self.state = rss_signage.TodoistState(self.store)
        self.state.tasks = [task("a"),task("child","a")]

    @patch("todoist_api._request_json")
    def test_external_completion_sync_covers_both_own_actor_formats(self, request):
        when = self.clock() - timedelta(minutes=1)
        events = [activity("browser", "outside-browser", when),
                  activity("phone", "outside-phone", when, actor=None),
                  activity("collaborator", "someone-else", when, actor="other")]

        def respond(token, path, params, timeout, verify_ssl):
            if path == "/user":
                return {"id": "me"}
            self.assertEqual(path, "/activities")
            # Todoist can represent our own actions with a null initiator.
            # A server-side user-ID filter can omit that representation.
            selected = [e for e in events if "initiator_id" not in params
                        or e["initiator_id"] == params["initiator_id"]]
            return {"results": selected, "next_cursor": None}

        request.side_effect = respond
        for _ in range(2):
            rss_signage.sync_activity(self.state, "token", 2, True)
            progress = self.state.dashboard()["progress"]
            self.assertEqual(progress["todoist_xp"], 20)
            self.assertEqual({e["task_id"] for e in progress["todoist_completed"]},
                             {"outside-browser", "outside-phone"})
            self.assertFalse(self.state.activity_errors)
        self.assertEqual([t.id for t in self.state.tasks], ["a", "child"])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM todoist_events WHERE id LIKE 'local:%'").fetchone()[0], 0)

        events.append(activity("phone-reopened", "outside-phone", when + timedelta(seconds=1),
                               "uncompleted", actor=None))
        rss_signage.sync_activity(self.state, "token", 2, True)
        self.assertEqual(self.state.dashboard()["progress"]["todoist_xp"], 10)

    def post(self,path,body=None,origin="http://localhost:8080"):
        handler = object.__new__(rss_signage.make_handler(self.state,"test-token"))
        data = json.dumps(body).encode()
        handler.path = path
        handler.headers = {"Host":"localhost:8080","Origin":origin,"Content-Type":"application/json","Content-Length":str(len(data))}
        handler.rfile = io.BytesIO(data)
        handler._json = MagicMock()
        handler.do_POST()
        args,kwargs = handler._json.call_args
        return kwargs.get("status",200),args[0]

    def test_save_and_reload_api_progress(self):
        status,payload = self.post("/api/quests/research",{"day":self.store.today(),"status":"bonus"})
        self.assertEqual(status,200)
        self.assertEqual(payload["progress"]["today_xp"],10)
        self.assertEqual(self.state.dashboard()["progress"]["today_xp"],10)
        self.assertFalse(payload["progress"]["bonus_unlocked"])

    def test_numeric_api_and_conflict_detection(self):
        body = {"day": self.store.today(), "amount": 1, "expected_amount": 0}
        status, payload = self.post("/api/quests/pushups", body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["progress"]["quests"][1]["amount"], 1)
        self.assertEqual(self.post("/api/quests/pushups", {**body, "amount": 2})[0], 409)
        self.assertEqual(self.store.snapshot()["quests"][1]["amount"], 1)

    def test_null_amount_is_rejected(self):
        self.assertEqual(self.post("/api/quests/pushups", {"day": self.store.today(), "amount": None})[0], 400)

    def test_cross_origin_quest_write_rejected(self):
        status,_ = self.post("/api/quests/research",{"day":self.store.today(),"status":"bonus"},"https://attacker.test")
        self.assertEqual(status,403)
        self.assertEqual(self.store.snapshot()["total_xp"],0)

    @patch("rss_signage.complete_task")
    def test_close_awards_only_on_success_and_repeat_is_not_awarded(self, close):
        close.side_effect = todoist_api.TodoistError("offline")
        self.assertEqual(self.post("/api/tasks/a/complete")[0],502)
        self.assertEqual(self.store.snapshot()["total_xp"],0)
        close.side_effect = None
        self.assertEqual(self.post("/api/tasks/a/complete")[0],200)
        self.assertEqual(self.store.snapshot()["total_xp"],10)
        self.assertEqual(self.post("/api/tasks/a/complete")[0],404)
        self.assertEqual(self.store.snapshot()["total_xp"],10)

    @patch("rss_signage.complete_task")
    def test_recurring_parent_does_not_remove_active_children(self,close):
        self.state.tasks[0].is_recurring = True
        self.assertEqual(self.post("/api/tasks/a/complete")[0],200)
        self.assertEqual([t.id for t in self.state.tasks],["child"])

    @patch("rss_signage.current_user_id",return_value="me")
    @patch("rss_signage.collect_activity",side_effect=todoist_api.TodoistError("offline"))
    def test_activity_failure_preserves_xp_and_reports_staleness(self,activity,user):
        self.store.record_local_completion("a","task")
        rss_signage.sync_activity(self.state,"token",2,True)
        self.assertTrue(self.state.activity_errors)
        self.assertEqual(self.store.snapshot()["total_xp"],10)


if __name__ == "__main__":
    unittest.main()
