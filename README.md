# Daily Quest + Todoist Signage

Show daily quests, overall and per-quest levels, and today's Todoist tasks in one
auto-updating local screen. The 11 quests and growth rules come from the
[November 7, 2026 design](https://docs.google.com/document/d/1qmO2UTIrlvSu69TfPazGqAFNC-Nzq3Tg6FiTV2wRcRI/edit).

## Daily quests and experience

- Enter each quest's **daily cumulative amount** (km, repetitions, minutes, or
  words) and press **記録**. Quick buttons add **+0.1 km** or **+1 repetition /
  minute / word** to the input's amount and save it. Normal-target attainment
  automatically awards 10 XP and 1 GP. Values below the target still persist.
- The **blue normal stage** shows only normal targets. Once **all 11 normal
  targets** are reached, the screen changes to the **red bonus stage** and shows
  bonus targets. Targets are still cumulative totals for the same day. Excess
  quantities already entered carry over; bonus attainment gives a total of
  20 XP and 2 GP per quest. Todoist tasks do not count toward the 11 normal
  completions. Quest controls are quantity entry, record, and increment.
- Correcting an amount below its normal target returns the screen to the blue
  stage and recalculates all of today's XP/GP. Repeated saves and re-unlocks do
  not duplicate awards. Concurrent edits from an out-of-date browser receive a
  conflict message instead of overwriting a newer measurement. Draft input is
  preserved while the dashboard refreshes. A Japan midnight rollover starts a
  fresh blue stage with zero amounts.
- Existing status-only records migrate automatically: previous days' awards
  remain unchanged; today's achieved target becomes its initial amount. The
  new stage rules are then applied to today's results.
- Each quest levels up after accumulating **20 GP and 3 bonus days at its current
  level**. A promotion consumes 20 GP and resets its bonus-day counter. Excess
  GP carries over. At most one promotion is possible per quest per day; new
  targets apply **the following day**. The first 14 calendar days hold growth.
- Click a quest's name for its qualifying work, cap, physical safety
  confirmation, and daily growth hold. Physical quests count bonus days and
  permit promotion only with the form/recovery confirmation. Caps stop target
  increases, but do not stop XP awards.
- Overall level is **1 + floor(total XP / 100)**. This is an added dashboard rule;
  it is independent of each quest's GP and target progression.
- Completing a Todoist task awards **10 XP per task per Japan calendar day**.
  Repeated completion of the same task that day does not multiply XP. Recurring
  tasks can earn XP again the next day. Todoist XP does not change quest GP.
  A successful completion here awards XP immediately. Completions made in
  Todoist are read from its activity log (including recurring tasks), normally
  every 60 seconds, and the browser checks for changes every 15 seconds.
  Completions from the phone app, web app, or another integration count even
  when the task is outside the dashboard's visible date filter. Both own-user
  ID and null-initiator activity records are accepted; collaborators are excluded.
  Reopening a task removes its latest award at the next sync.
- Only your own Todoist completion/reopen events are counted, starting at
  midnight Japan time on the day this feature is first run. Past days before
  installation are not imported. Missing/deleted active tasks alone never earn XP.
- All records live in `data/quests.sqlite3` (ignored by git), survive restarts,
  and switch days at **00:00 Asia/Tokyo**. Keep this file when updating the code.
  To back up a running database, use SQLite's backup API rather than copying a
  live WAL database's main file alone. `--data-file <path>` selects another file.
- The imported design is implemented locally; changes to the Google Doc are
  not automatically imported. Google credentials are not required at runtime.

The default view shows today's and overdue tasks together, with overdue dates
marked in red. **取得した全件** shows the full fetched range, **更新** immediately
refreshes Todoist, and **今日の完了** shows today's credited completions. Each
panel scrolls to show all its items. The Todoist panel advances one task every
15 seconds, keeping its position through data refreshes. Rotation pauses while
scrolled down, using task controls, or recording a completion; return to the top
and leave the controls to resume. **一時停止 / 再開** also controls rotation.
Quest names and goals use larger type; click a name for GP, bonus-day progress,
and safety controls. **文字 → 特大** further increases text size and remembers
the preference in this browser. On smaller screens the lists can scroll.
If a refresh fails, the previous data stays visible with a sync error. Local
quest records continue to work while Todoist is unavailable. The activity-log
retention window depends on the Todoist plan; a long outage beyond that window
can leave external completions unavailable for import.

The signage server uses the official Todoist REST API. A normal Todoist browser login cannot be reused by this localhost app because browser cookies and local storage are isolated by origin, so provide an API token to the Python server instead.

## Setup

Create a local `.env` file:

```bash
TODOIST_API_TOKEN=your_todoist_api_token_here
```

`TODOIST_API` and `TODOIST_TOKEN` are also accepted as variable names.

`.env` is already ignored by git.

## Run

```bash
python3 rss_signage.py --refresh-seconds 60 --port 8080
```

Open:

- `http://localhost:8080/` (daily quest / level / Todoist dashboard)
- `http://localhost:8080/stream` (previous rotating task signage screen)
- `http://localhost:8080/api/dashboard` (combined dashboard data)
- `http://localhost:8080/api/tasks` (JSON API)
- `http://localhost:8080/healthz` (health check)

Each task has a **完了** (Complete) checkbox. Checking it completes the task in
Todoist and removes it and its subtasks from the screen. The task remains in
Todoist's completed history. The checkbox is disabled while completion is in
progress. If completion fails, the task stays in the list, an error appears,
and you can check it again to retry. Click the task text to open it in Todoist.

The browser sends `POST /api/tasks/<task-id>/complete` to the local server, which
calls Todoist's `POST /api/v1/tasks/<task-id>/close` endpoint. The Todoist API token
stays on the server. Task counts update after a successful completion. The old
`DELETE` endpoint is disabled; reload any already-open tabs before checking tasks.

Recurring tasks follow Todoist's normal completion behavior and advance to their
next occurrence. They may reappear after a refresh if the new date matches the
filter.

By default, the screen shows Todoist tasks matching:

```text
next 7 days | overdue
```

## Autostart (launchd)

On the signage machine the server is managed by a LaunchAgent (`KeepAlive`, so it restarts automatically):

```bash
# restart after changing code or the plist
launchctl unload ~/Library/LaunchAgents/com.yasushi.newsfeed.rss-signage.plist
launchctl load ~/Library/LaunchAgents/com.yasushi.newsfeed.rss-signage.plist
```

The plist runs `rss_signage.py --refresh-seconds 60 --port 8080` from this directory. Logs: `~/Library/Logs/newsfeed-rss-signage.{out,err}.log`.

## Useful Flags

- `--filter "next 7 days | overdue"` show upcoming and overdue tasks
- `--filter "today | overdue"` show only today's and overdue tasks
- `--all` show all active tasks instead of using `--filter`
- `--project-id <id>` filter by project when using `--all`
- `--section-id <id>` filter by section when using `--all`
- `--label <name>` filter by label when using `--all`
- `--limit 240` keep many tasks in the rotation pool
- The legacy `/stream` screen rotates the visible task list every 15 seconds.
- Scroll the task list to browse all fetched tasks. Automatic rotation pauses while scrolled down and resumes when you return to the top.
- Past due dates are red. Date-only tasks become overdue the following day in the browser's local time; timed tasks become overdue after their due time.
- Due colors update while the page is open, even if Todoist is temporarily unreachable. Failed refreshes keep the last successful task list and retry automatically.
- `--refresh-seconds 60` poll Todoist every 60 seconds
- `--insecure` disable SSL verification only when your environment has certificate issues

## Legacy RSS Scripts

`rss_news.py`, `rss_feed.py`, and `feeds.txt` are still present for the old RSS CLI workflow, but the signage server now reads Todoist.

## Verification

```bash
python3 -m unittest -v test_quest_amounts test_quests test_task_completion
node --check static/dashboard.js
```

Tests use isolated SQLite databases and mocked Todoist calls. They cover target
values, XP corrections, calibration, promotion and caps, recovery holds, Japan
midnight rollover, persistence, activity pagination/deduplication/reopen, and
completion failures and races. They do not complete real Todoist tasks.

## Public Repo Safety

- Do not commit secrets (`.env`, API tokens, certificate/private key files).
- If a secret was ever committed, rotate it and rewrite git history before publishing.
