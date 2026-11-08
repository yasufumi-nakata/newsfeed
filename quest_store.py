"""Persistent daily quests and Todoist XP, using the 2026-11-07 design rules."""

import math
import sqlite3
import threading
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
SOURCE_URL = "https://docs.google.com/document/d/1qmO2UTIrlvSu69TfPazGqAFNC-Nzq3Tg6FiTV2wRcRI/edit"
STATUS_POINTS = {"missed": (0, 0), "normal": (10, 1), "bonus": (20, 2), "rest": (0, 0)}
TODOIST_XP = 10
LEVEL_XP = 100
CALIBRATION_DAYS = 14


class QuestConflict(ValueError):
    """The amount changed since this browser last read it."""

# id, name, unit, initial bonus, increment, cap, category, qualifying work
QUESTS = (
    ("walk", "速歩き", "km", 5, 1, 10, "身体", "無理のない速歩きの1日合計距離。体力に合わせた速さで。"),
    ("pushups", "腕立て伏せ", "回", 10, 2, 30, "身体", "1日の合計回数。壁・台への変更可。比較時は条件をそろえる。"),
    ("squats", "スクワット", "回", 20, 4, 60, "身体", "1日の合計回数。"),
    ("listening", "英語リスニング", "分", 30, 4, 60, "英語", "英語音声を聴く。安全に配慮すれば速歩きと同時に計上可。"),
    ("speaking", "英語を話す", "分", 10, 2, 30, "英語", "研究の説明や相手への質問など、英語を声に出して使う。"),
    ("research", "研究", "分", 90, 10, 150, "活動", "優先する実験・解析・論文・文献・理論検討。関連する英語・数理練習は内数にできる。"),
    ("business", "起業", "分", 30, 4, 60, "活動", "最優先の顧客課題の検証・顧客対話・試作品開発・営業など。"),
    ("work", "仕事・事務", "分", 30, 4, 60, "活動", "最優先業務や必要な生活事務。達成のためだけに不要な仕事を作らない。"),
    ("reading", "英文を読む", "分", 20, 2, 30, "英語", "難度の合った英文教科書・論文などの原文を読む。日本語要約のみは対象外。"),
    ("writing", "英文を書く", "語", 100, 10, 200, "英語", "自分で組み立てた英文。研究メモ・日記・メール可。AI添削可、丸ごとの代筆は対象外。"),
    ("math", "数学・統計を計算する", "分", 20, 2, 30, "研究", "線形代数・確率統計・信号処理など。解説確認を含めてよく、完答は条件にしない。"),
)


def utc_iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timezone is required")
    return parsed


class QuestStore:
    def __init__(self, path, clock=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.RLock()
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS quest_records (
                day TEXT NOT NULL, quest_id TEXT NOT NULL, status TEXT NOT NULL,
                safe_bonus INTEGER NOT NULL DEFAULT 0, hold INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(day, quest_id)
            );
            CREATE TABLE IF NOT EXISTS todoist_events (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL,
                happened_at TEXT NOT NULL, day TEXT NOT NULL, title TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS todoist_task_events ON todoist_events(task_id, happened_at);
        """)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('started_on', ?)", (self.today(),))
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(quest_records)")}
            if "amount" not in columns:
                self.db.execute("ALTER TABLE quest_records ADD COLUMN amount REAL")
            # Earlier days keep their original awards. Today's old status-only
            # records inherit their achieved target as a starting measurement.
            if self.db.execute("SELECT 1 FROM quest_records WHERE day=? AND amount IS NULL LIMIT 1", (self.today(),)).fetchone():
                self._write_today(self.snapshot()["quests"], self.today())

    def close(self):
        with self.lock:
            self.db.close()

    def today(self):
        return self.clock().astimezone(JST).date().isoformat()

    def _meta(self, key, default=None):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def _set_meta(self, key, value):
        self.db.execute("INSERT INTO metadata VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def sync_since(self):
        with self.lock:
            start = datetime.combine(datetime.fromisoformat(self._meta("started_on")).date(), time.min, JST)
            last = self._meta("todoist_synced_at")
            # Re-read an overlap to pick up delayed activity records; event IDs deduplicate.
            return utc_iso(max(start, parse_timestamp(last) - timedelta(days=2))) if last else utc_iso(start)

    @staticmethod
    def _amount(value, unit):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1_000_000:
            raise ValueError("実績は0以上の数値で入力してください（上限1,000,000）。")
        if unit != "km" and value != int(value):
            raise ValueError("回数・分数・語数は整数で入力してください。")
        return round(value, 3) if unit == "km" else int(value)

    def _write_today(self, quests, day):
        unlocked = all(not q["rest"] and q["amount"] >= q["normal_target"] for q in quests)
        rows = []
        for q in quests:
            status = ("rest" if q["rest"] else "bonus" if unlocked and q["amount"] >= q["bonus_target"]
                      else "normal" if q["amount"] >= q["normal_target"] else "missed")
            rows.append((day, q["id"], status, q["safe_bonus"], q["hold"], q["amount"]))
        # One transaction recalculates all awards when the stage unlocks or a
        # correction locks it again. No incremental XP counters can drift.
        self.db.executemany("""INSERT INTO quest_records (day, quest_id, status, safe_bonus, hold, amount)
            VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(day, quest_id) DO UPDATE SET
            status=excluded.status, safe_bonus=excluded.safe_bonus, hold=excluded.hold, amount=excluded.amount""", rows)

    def save_quest(self, quest_id, day, status=None, safe_bonus=None, hold=None, *, amount=None, rest=None, expected_amount=None):
        if quest_id not in {q[0] for q in QUESTS}:
            raise ValueError("クエストが見つかりません。")
        if status is not None and (not isinstance(status, str) or status not in STATUS_POINTS):
            raise ValueError("記録は未達・通常・ボーナス・休養免除から選んでください。")
        if any(value is not None and type(value) is not bool for value in (safe_bonus, hold, rest)):
            raise ValueError("確認欄の値が正しくありません。")
        with self.lock, self.db:
            current = self.snapshot()
            if day != current["day"]:
                raise ValueError("日付が変わりました。画面を更新して今日の記録を選んでください。")
            quests = current["quests"]
            q = next(q for q in quests if q["id"] == quest_id)
            if expected_amount is not None:
                expected = self._amount(expected_amount, q["unit"])
                if not math.isclose(expected, q["amount"], abs_tol=1e-9):
                    raise QuestConflict("別の画面で実績が更新されました。最新の値を確認して記録し直してください。")
            if amount is not None:
                q["amount"] = self._amount(amount, q["unit"])
            elif status in ("normal", "bonus", "missed"):
                # Old open tabs remain usable, but cannot bypass the stage gate.
                q["amount"] = q["normal_target"] if status == "normal" else q["bonus_target"] if status == "bonus" else 0
            if rest is not None:
                q["rest"] = rest
            elif status is not None:
                q["rest"] = status == "rest"
            if safe_bonus is not None:
                q["safe_bonus"] = safe_bonus
            if hold is not None:
                q["hold"] = hold
            self._write_today(quests, day)
        return self.snapshot()

    def record_local_completion(self, task_id, title):
        now = self.clock()
        with self.lock, self.db:
            self.db.execute("INSERT INTO todoist_events VALUES (?, ?, 'completed', ?, ?, ?)",
                            ("local:" + uuid.uuid4().hex, task_id, utc_iso(now), now.astimezone(JST).date().isoformat(), title))

    def ingest_activity(self, events, synced_at):
        # Validate the entire batch before writing or advancing the watermark.
        normalized = []
        for event in events:
            if event.get("object_type") != "item" or event.get("event_type") not in ("completed", "uncompleted"):
                continue
            if not event.get("id") or not event.get("object_id") or not event.get("event_date"):
                raise ValueError("Todoist の完了履歴に必要な項目がありません。")
            happened = parse_timestamp(event["event_date"])
            normalized.append((str(event["id"]), str(event["object_id"]), event["event_type"], utc_iso(happened),
                               happened.astimezone(JST).date().isoformat(), str((event.get("extra_data") or {}).get("content") or "完了したタスク")))
        parse_timestamp(synced_at)
        with self.lock, self.db:
            started = self._meta("started_on")
            self.db.executemany("INSERT OR IGNORE INTO todoist_events VALUES (?, ?, ?, ?, ?, ?)",
                                [row for row in normalized if row[4] >= started])
            self._set_meta("todoist_synced_at", synced_at)

    def _todoist_awards(self):
        awards, latest = {}, {}
        for row in self.db.execute("SELECT * FROM todoist_events ORDER BY happened_at, id"):
            key = (row["task_id"], row["day"])
            if row["kind"] == "completed":
                awards[key] = {"task_id": row["task_id"], "day": row["day"], "title": row["title"], "xp": TODOIST_XP}
                latest[row["task_id"]] = key
            else:
                # Reopening removes that task's most recent award, including yesterday's.
                previous = latest.pop(row["task_id"], None)
                if previous:
                    awards.pop(previous, None)
        return list(awards.values())

    def snapshot(self):
        with self.lock:
            today = self.today()
            started = self._meta("started_on")
            start_date = datetime.fromisoformat(started).date()
            day_number = (datetime.fromisoformat(today).date() - start_date).days + 1
            records = list(self.db.execute("SELECT * FROM quest_records WHERE day<=? ORDER BY day", (today,)))
            quests = []
            total_quest_xp = sum(STATUS_POINTS[r["status"]][0] for r in records)
            for quest_id, name, unit, initial, increment, cap, category, description in QUESTS:
                level, gp, bonus_days = 1, 0, 0
                current = {"status": "missed", "safe_bonus": False, "hold": False}
                amount = None
                displayed_level, pending = None, False
                for row in (r for r in records if r["quest_id"] == quest_id):
                    if row["day"] == today:
                        displayed_level = level
                        current = {k: row[k] if k == "status" else bool(row[k]) for k in current}
                        amount = row["amount"]
                    gp += STATUS_POINTS[row["status"]][1]
                    if row["status"] == "bonus" and (category != "身体" or row["safe_bonus"]):
                        bonus_days += 1
                    age = (datetime.fromisoformat(row["day"]).date() - start_date).days
                    safe = category != "身体" or row["safe_bonus"]
                    if (age >= CALIBRATION_DAYS and not row["hold"] and safe
                            and row["status"] in ("normal", "bonus") and gp >= 20 and bonus_days >= 3
                            and initial + (level - 1) * increment < cap):
                        level += 1
                        gp -= 20
                        bonus_days = 0
                        pending = row["day"] == today
                displayed_level = displayed_level or level
                bonus = min(initial + (displayed_level - 1) * increment, cap)
                normal = math.ceil(bonus) / 2 if unit == "km" else math.ceil(bonus / 2)
                next_bonus = min(initial + (level - 1) * increment, cap)
                if amount is None:
                    amount = bonus if current["status"] == "bonus" else normal if current["status"] == "normal" else 0
                quests.append({"id": quest_id, "name": name, "unit": unit, "category": category, "description": description,
                               "level": displayed_level, "gp": gp, "bonus_days": bonus_days,
                               "normal_target": normal, "bonus_target": bonus, "cap": cap,
                               "at_cap": bonus >= cap, "promotion_pending": pending,
                               "next_level": level, "next_bonus_target": next_bonus,
                               "amount": amount, "step": 0.1 if unit == "km" else 1, "rest": current["status"] == "rest",
                               "normal_complete": current["status"] in ("normal", "bonus"),
                               "xp": STATUS_POINTS[current["status"]][0], **current})
            unlocked = all(q["normal_complete"] for q in quests)
            for q in quests:
                q["target"] = q["bonus_target"] if unlocked else q["normal_target"]
                q["remaining"] = max(0, round(q["target"] - q["amount"], 3))
            awards = self._todoist_awards()
            today_awards = [a for a in awards if a["day"] == today]
            quest_today = sum(q["xp"] for q in quests)
            total_xp = total_quest_xp + len(awards) * TODOIST_XP
            return {"day": today, "timezone": "Asia/Tokyo", "started_on": started, "day_number": day_number,
                    "calibration_days_left": max(0, CALIBRATION_DAYS - day_number + 1),
                    "source_url": SOURCE_URL, "quests": quests,
                    "bonus_unlocked": unlocked, "stage": "bonus" if unlocked else "normal",
                    "bonus_done": sum(q["status"] == "bonus" for q in quests),
                    "total_xp": total_xp, "level": total_xp // LEVEL_XP + 1,
                    "level_progress": total_xp % LEVEL_XP, "level_span": LEVEL_XP,
                    "today_xp": quest_today + len(today_awards) * TODOIST_XP,
                    "quest_xp": quest_today, "todoist_xp": len(today_awards) * TODOIST_XP,
                    "quest_done": sum(q["status"] in ("normal", "bonus") for q in quests),
                    "quest_rest": sum(q["status"] == "rest" for q in quests),
                    "todoist_completed": today_awards, "todoist_synced_at": self._meta("todoist_synced_at"),
                    "todoist_xp_per_task": TODOIST_XP}
