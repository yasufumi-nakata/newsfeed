# RSS News Signage

Fetch RSS/Atom feeds and show them in an auto-updating signage screen.

## 1) CLI fetch

```bash
python3 rss_news.py "https://feeds.bbci.co.uk/news/rss.xml" --limit 10
```

JSON output:

```bash
python3 rss_news.py "https://feeds.bbci.co.uk/news/rss.xml" --json
```

## 2) Signage mode (auto refresh, English news wall)

```bash
python3 rss_signage.py --refresh-seconds 300 --port 8080 --insecure
```

Open:

- `http://localhost:8080/` (signage screen)
- `http://localhost:8080/api/news` (JSON API)

Default behavior:

- If `feeds.txt` exists, it is loaded automatically (comments and blank lines are ignored).
- If `feeds.txt` is missing/empty, built-in feeds are used.

Use your own feeds file:

```bash
python3 rss_signage.py --feeds-file feeds.txt
```

Or pass feeds directly:

```bash
python3 rss_signage.py "https://feeds.bbci.co.uk/news/rss.xml" "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"
```

## Useful flags

- `--keyword economy` filter by keyword
- `--limit 240` keep many headlines
- `--refresh-seconds 60` poll feeds every 60 seconds
- `--insecure` disable SSL verification (use only when your environment has cert issues)
