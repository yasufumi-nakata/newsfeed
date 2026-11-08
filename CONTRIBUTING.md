# Contributing

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install build
```

## Checks

```bash
python -m compileall -q rss_feed.py rss_news.py rss_signage.py quest_store.py todoist_api.py
python -m unittest test_quest_amounts test_quests test_task_completion
node --check static/dashboard.js
python -m build
rss-news --help
rss-signage --help
```

## Pull Requests

- Keep feed examples public and non-secret.
- Put private feed URLs in `feeds.local.txt`; do not commit them.
- Preserve the no-dependency standard-library runtime unless a dependency clearly reduces risk.
