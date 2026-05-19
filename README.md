# RSS News Signage

Fetch RSS/Atom feeds and show them in an auto-updating signage screen.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

## 1) CLI fetch

```bash
rss-news "https://feeds.bbci.co.uk/news/rss.xml" --limit 10
```

JSON output:

```bash
rss-news "https://feeds.bbci.co.uk/news/rss.xml" --json
```

## 2) Signage mode (auto refresh, English news wall)

```bash
rss-signage --refresh-seconds 300 --port 8080 --insecure
```

Open:

- `http://localhost:8080/` (signage screen)
- `http://localhost:8080/api/news` (JSON API)

Default behavior:

- If `feeds.txt` exists, it is loaded automatically (comments and blank lines are ignored).
- If `feeds.txt` is missing/empty, built-in feeds are used.

Use your own feeds file:

```bash
rss-signage --feeds-file feeds.txt
```

Or pass feeds directly:

```bash
rss-signage "https://feeds.bbci.co.uk/news/rss.xml" "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"
```

## Useful flags

- `--keyword economy` filter by keyword
- `--limit 240` keep many headlines
- `--refresh-seconds 60` poll feeds every 60 seconds
- `--insecure` disable SSL verification (use only when your environment has cert issues)

## Public repo safety

- Do not commit secrets (`.env`, API tokens in URLs, certificate/private key files).
- Keep private feed URLs in `feeds.local.txt` and run with:
  `python3 rss_signage.py --feeds-file feeds.local.txt`
- If a secret was ever committed, rotate it and rewrite git history before publishing.
