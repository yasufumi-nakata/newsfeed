#!/usr/bin/env python3
"""Serve RSS headlines in an auto-updating signage view."""

import argparse
import json
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List, Optional

from rss_feed import NewsEntry, collect_entries

DEFAULT_FEEDS = [
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "https://www.npr.org/rss/rss.php?id=1001",
    "https://rss.cnn.com/rss/edition.rss",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://feeds.skynews.com/feeds/rss/home.xml",
    "https://www.theguardian.com/world/rss",
    "https://www.engadget.com/rss.xml",
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://moxie.foxnews.com/google-publisher/latest.xml",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://www.wired.com/feed/rss",
]

HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Global News Stream</title>
  <style>
    :root {
      --bg-1: #09273d;
      --bg-2: #041320;
      --panel: rgba(255, 255, 255, 0.12);
      --panel-2: rgba(255, 255, 255, 0.08);
      --line: rgba(255, 255, 255, 0.24);
      --text: #f2f7ff;
      --muted: #bfd1e4;
      --accent: #ffd166;
      --good: #7be0a9;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at 0% 0%, rgba(255, 209, 102, 0.22), transparent 40%),
        radial-gradient(circle at 100% 100%, rgba(71, 149, 221, 0.30), transparent 55%),
        linear-gradient(150deg, var(--bg-1), var(--bg-2));
      color: var(--text);
      font-family: "SF Pro Text", "Hiragino Sans", "Noto Sans JP", "Segoe UI", sans-serif;
      text-rendering: optimizeLegibility;
      -webkit-font-smoothing: antialiased;
      display: grid;
      grid-template-rows: auto 1fr;
      overflow: hidden;
    }

    header {
      padding: 1rem 1.4rem 0.95rem;
      border-bottom: 1px solid var(--line);
      background: rgba(2, 11, 18, 0.36);
      display: grid;
      gap: 0.5rem;
    }

    .title-row {
      display: flex;
      justify-content: space-between;
      gap: 1rem;
      align-items: baseline;
    }

    .title {
      font-size: clamp(1.55rem, 3.8vw, 2.7rem);
      font-weight: 700;
      letter-spacing: 0.035em;
      text-transform: uppercase;
    }

    .clock {
      font-size: clamp(1.15rem, 2.5vw, 1.9rem);
      color: var(--muted);
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
      font-weight: 600;
    }

    .meta-row {
      display: flex;
      gap: 0.7rem;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: clamp(0.95rem, 1.35vw, 1.18rem);
      font-weight: 500;
    }

    .pill {
      padding: 0.38rem 0.62rem;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--panel-2);
      white-space: nowrap;
    }

    #source-count { color: var(--good); }
    #queue-count { color: var(--accent); }

    main {
      overflow: hidden;
      padding: 0.92rem 1.2rem 1.1rem;
    }

    .stream {
      height: 100%;
      display: grid;
      gap: 0.78rem;
      align-content: start;
    }

    .story {
      display: grid;
      gap: 0.45rem;
      grid-template-rows: auto auto 1fr auto;
      min-height: 190px;
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 0.9rem 1rem;
      text-decoration: none;
      color: inherit;
      background: var(--panel);
      box-shadow: 0 18px 40px rgba(0, 0, 0, 0.22);
      transition: transform 200ms ease, border-color 200ms ease;
      animation: story-in 360ms ease both;
    }

    .story:hover {
      transform: translateY(-2px);
      border-color: rgba(255, 209, 102, 0.7);
    }

    .story.new {
      border-color: rgba(123, 224, 169, 0.9);
      box-shadow: 0 0 0 1px rgba(123, 224, 169, 0.32), 0 18px 40px rgba(0, 0, 0, 0.22);
    }

    @keyframes story-in {
      from {
        opacity: 0;
        transform: translateY(-8px);
      }
      to {
        opacity: 1;
        transform: translateY(0);
      }
    }

    .story-source {
      color: var(--accent);
      font-size: clamp(0.95rem, 1.35vw, 1.15rem);
      text-transform: uppercase;
      letter-spacing: 0.04em;
      font-weight: 600;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .story-title {
      font-size: clamp(1.28rem, 2.15vw, 1.95rem);
      line-height: 1.24;
      font-weight: 700;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }

    .story-summary {
      color: #deebf8;
      font-size: clamp(1.02rem, 1.36vw, 1.24rem);
      line-height: 1.35;
      font-weight: 500;
      display: -webkit-box;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }

    .story-meta {
      color: var(--muted);
      font-size: clamp(0.95rem, 1.2vw, 1.05rem);
      font-variant-numeric: tabular-nums;
      font-weight: 500;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }

    .empty {
      border: 1px dashed var(--line);
      border-radius: 16px;
      min-height: 200px;
      display: grid;
      place-items: center;
      color: var(--muted);
      font-size: clamp(1rem, 1.45vw, 1.25rem);
      background: rgba(255, 255, 255, 0.06);
      text-align: center;
      padding: 1rem;
    }

    @media (max-width: 980px) {
      header {
        padding: 0.86rem 0.95rem 0.74rem;
      }
      main {
        padding: 0.72rem 0.8rem 0.85rem;
      }
      .story {
        min-height: 168px;
        padding: 0.76rem 0.82rem;
      }
      .story-title {
        -webkit-line-clamp: 3;
      }
      .story-summary {
        -webkit-line-clamp: 2;
      }
    }

    @media (max-width: 640px) {
      .title {
        font-size: clamp(1.2rem, 5.3vw, 1.7rem);
      }
      .clock {
        font-size: 1rem;
      }
      .meta-row {
        gap: 0.5rem;
        font-size: 0.9rem;
      }
      .story {
        min-height: 154px;
      }
      .story-summary {
        -webkit-line-clamp: 2;
      }
    }
  </style>
</head>
<body>
  <header>
    <div class="title-row">
      <div class="title">Global News Stream</div>
      <div class="clock" id="clock">--:--:--</div>
    </div>
    <div class="meta-row">
      <span class="pill" id="source-count">0 sources</span>
      <span class="pill" id="headline-count">0 headlines</span>
      <span class="pill" id="queue-count">queue 0</span>
      <span class="pill" id="status">Waiting for first update...</span>
    </div>
  </header>
  <main>
    <section class="stream" id="stream" aria-live="polite"></section>
  </main>
  <script>
    const DISPLAY_ADVANCE_SECONDS = 120;

    let queue = [];
    let visibleEntries = [];
    let lastUpdatedIso = null;
    let lastErrorCount = 0;
    let lastErrorMessage = "";
    let fetchedCount = 0;
    let fetchedSourceCount = 0;
    let nextAdvanceAtMs = Date.now() + DISPLAY_ADVANCE_SECONDS * 1000;
    let fetching = false;
    let advancing = false;

    const streamEl = document.getElementById("stream");
    const statusEl = document.getElementById("status");
    const sourceCountEl = document.getElementById("source-count");
    const headlineCountEl = document.getElementById("headline-count");
    const queueCountEl = document.getElementById("queue-count");
    const clockEl = document.getElementById("clock");

    function entryKey(entry) {
      return entry.link || `${entry.source}|${entry.title}|${entry.published || ""}`;
    }

    function uniqueEntries(entries) {
      const seen = new Set();
      const unique = [];
      for (const entry of entries) {
        const key = entryKey(entry);
        if (seen.has(key)) continue;
        seen.add(key);
        unique.push(entry);
      }
      return unique;
    }

    function toLocal(iso) {
      if (!iso) return "Unknown time";
      const date = new Date(iso);
      if (Number.isNaN(date.getTime())) return "Unknown time";
      return date.toLocaleString();
    }

    function hostFromUrl(link) {
      if (!link) return "";
      try {
        return new URL(link).hostname.replace(/^www\\./, "");
      } catch (_err) {
        return "";
      }
    }

    function computeVisibleCount() {
      const h = window.innerHeight;
      const w = window.innerWidth;
      if (w < 700) {
        return h < 760 ? 3 : 4;
      }
      if (h < 760) return 4;
      if (h < 900) return 5;
      return 6;
    }

    function syncPills() {
      headlineCountEl.textContent = `${fetchedCount} headlines`;
      sourceCountEl.textContent = `${fetchedSourceCount} sources`;
      queueCountEl.textContent = `queue ${queue.length}`;
    }

    function renderStatus() {
      const remain = Math.max(0, Math.ceil((nextAdvanceAtMs - Date.now()) / 1000));
      if (lastErrorMessage) {
        statusEl.textContent = `Update failed: ${lastErrorMessage} | retry in ${remain}s`;
        return;
      }
      if (!lastUpdatedIso) {
        statusEl.textContent = `Waiting for first update... | next change ${remain}s`;
        return;
      }
      const updated = toLocal(lastUpdatedIso);
      const warnText = lastErrorCount ? ` | WARN ${lastErrorCount}` : "";
      statusEl.textContent = `Updated ${updated}${warnText} | next change ${remain}s`;
    }

    function renderStream(highlightKey = "") {
      const visibleCount = computeVisibleCount();
      const visible = visibleEntries.slice(0, visibleCount);

      streamEl.textContent = "";
      if (!visible.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "No entries available. Check feed URLs or SSL settings.";
        streamEl.appendChild(empty);
        return;
      }

      for (const entry of visible) {
        const key = entryKey(entry);
        const story = document.createElement("a");
        story.className = "story";
        if (highlightKey && key === highlightKey) {
          story.classList.add("new");
        }
        story.target = "_blank";
        story.rel = "noreferrer noopener";
        story.href = entry.link || "#";

        const source = document.createElement("div");
        source.className = "story-source";
        source.textContent = entry.source || "Unknown Source";

        const title = document.createElement("div");
        title.className = "story-title";
        title.textContent = entry.title || "Untitled";

        const summary = document.createElement("div");
        summary.className = "story-summary";
        summary.textContent = entry.summary || "No summary provided by this source.";

        const meta = document.createElement("div");
        meta.className = "story-meta";
        const parts = [toLocal(entry.published)];
        if (entry.author) parts.push(`By ${entry.author}`);
        const host = hostFromUrl(entry.link);
        if (host) parts.push(host);
        meta.textContent = parts.join(" | ");

        story.appendChild(source);
        story.appendChild(title);
        story.appendChild(summary);
        story.appendChild(meta);
        streamEl.appendChild(story);
      }
    }

    async function fetchQueue() {
      if (fetching) return false;
      fetching = true;
      try {
        const response = await fetch("/api/news", { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const payload = await response.json();
        const fresh = uniqueEntries(payload.entries || []);
        queue = fresh;
        fetchedCount = fresh.length;
        fetchedSourceCount = new Set(fresh.map((entry) => entry.source || "").filter(Boolean)).size;
        lastUpdatedIso = payload.updated_at || null;
        lastErrorCount = (payload.errors || []).length;
        lastErrorMessage = "";
        syncPills();
        return true;
      } catch (error) {
        lastErrorMessage = error.message || "unknown error";
        return false;
      } finally {
        fetching = false;
      }
    }

    function shiftNextToTop() {
      if (!queue.length) return "";
      const next = queue.shift();
      const key = entryKey(next);
      const targetSize = computeVisibleCount();

      visibleEntries.unshift(next);
      if (visibleEntries.length > targetSize) {
        visibleEntries.length = targetSize;
      }
      syncPills();
      return key;
    }

    async function advanceStream() {
      if (advancing) return;
      advancing = true;
      try {
        if (!queue.length) {
          const ok = await fetchQueue();
          if (!ok) {
            nextAdvanceAtMs = Date.now() + DISPLAY_ADVANCE_SECONDS * 1000;
            renderStatus();
            return;
          }
        }

        const highlightKey = shiftNextToTop();
        renderStream(highlightKey);
        nextAdvanceAtMs = Date.now() + DISPLAY_ADVANCE_SECONDS * 1000;
        renderStatus();

        if (!queue.length) {
          fetchQueue();
        }
      } finally {
        advancing = false;
      }
    }

    function tickClock() {
      clockEl.textContent = new Date().toLocaleTimeString();
    }

    let resizeTimer = null;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        const target = computeVisibleCount();
        if (visibleEntries.length > target) {
          visibleEntries.length = target;
        }
        while (visibleEntries.length < target && queue.length) {
          visibleEntries.push(queue.shift());
        }
        syncPills();
        renderStream();
      }, 220);
    });

    (async () => {
      tickClock();
      renderStatus();
      await fetchQueue();

      const target = computeVisibleCount();
      while (visibleEntries.length < target && queue.length) {
        visibleEntries.push(queue.shift());
      }
      syncPills();
      renderStream();
      nextAdvanceAtMs = Date.now() + DISPLAY_ADVANCE_SECONDS * 1000;
      renderStatus();

      setInterval(advanceStream, DISPLAY_ADVANCE_SECONDS * 1000);
      setInterval(() => {
        tickClock();
        renderStatus();
      }, 1000);
    })();
  </script>
</body>
</html>
"""


class FeedState:
    def __init__(self) -> None:
        self.entries: List[NewsEntry] = []
        self.errors: List[str] = []
        self.updated_at: Optional[str] = None
        self.lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_feeds_file(path: str) -> List[str]:
    file_path = Path(path)
    if not file_path.exists():
        return []

    feeds: List[str] = []
    seen = set()
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line in seen:
            continue
        seen.add(line)
        feeds.append(line)
    return feeds


def refresh_once(
    state: FeedState,
    urls: List[str],
    timeout: float,
    keyword: Optional[str],
    limit: int,
    verify_ssl: bool,
) -> None:
    entries, errors = collect_entries(
        urls=urls,
        timeout=timeout,
        keyword=keyword,
        limit=limit,
        verify_ssl=verify_ssl,
    )
    with state.lock:
        state.entries = entries
        state.errors = errors
        state.updated_at = now_iso()


def refresh_loop(
    state: FeedState,
    urls: List[str],
    timeout: float,
    keyword: Optional[str],
    limit: int,
    verify_ssl: bool,
    interval: float,
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        if stop_event.wait(interval):
            break
        refresh_once(
            state,
            urls=urls,
            timeout=timeout,
            keyword=keyword,
            limit=limit,
            verify_ssl=verify_ssl,
        )


def make_handler(state: FeedState):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, payload: object, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _html(self, payload: str) -> None:
            body = payload.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", maxsplit=1)[0]
            if path == "/":
                self._html(HTML)
                return

            if path == "/api/news":
                with state.lock:
                    payload = {
                        "updated_at": state.updated_at,
                        "entries": [asdict(item) for item in state.entries],
                        "errors": list(state.errors),
                    }
                self._json(payload)
                return

            if path == "/healthz":
                self._json({"ok": True, "updated_at": state.updated_at})
                return

            self._json({"error": "not found"}, status=404)

        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            return

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="RSS signage server with auto-refresh.")
    parser.add_argument("urls", nargs="*", help="RSS or Atom feed URLs")
    parser.add_argument("--feeds-file", default="feeds.txt", help="Path to a feed list file")
    parser.add_argument("--bind", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port")
    parser.add_argument("--limit", type=int, default=240, help="Max number of entries to keep")
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout (seconds)")
    parser.add_argument("--keyword", help="Filter entries by keyword")
    parser.add_argument("--insecure", action="store_true", help="Disable SSL certificate verification")
    parser.add_argument("--refresh-seconds", type=float, default=300.0, help="RSS refresh interval")
    args = parser.parse_args()

    if args.urls:
        urls = args.urls
        print(f"[INFO] Using {len(urls)} feed URLs from CLI arguments.")
    else:
        urls = load_feeds_file(args.feeds_file)
        if urls:
            print(f"[INFO] Loaded {len(urls)} feeds from {args.feeds_file}.")
        else:
            urls = DEFAULT_FEEDS
            print("[INFO] No feed URLs supplied and feed file missing/empty. Using built-in feeds.")

    state = FeedState()
    refresh_once(
        state,
        urls=urls,
        timeout=args.timeout,
        keyword=args.keyword,
        limit=args.limit,
        verify_ssl=not args.insecure,
    )
    print(f"[INFO] Loaded {len(state.entries)} entries at startup from {len(urls)} feeds.")

    stop_event = threading.Event()
    thread = threading.Thread(
        target=refresh_loop,
        kwargs={
            "state": state,
            "urls": urls,
            "timeout": args.timeout,
            "keyword": args.keyword,
            "limit": args.limit,
            "verify_ssl": not args.insecure,
            "interval": max(args.refresh_seconds, 5.0),
            "stop_event": stop_event,
        },
        daemon=True,
    )
    thread.start()

    server = ThreadingHTTPServer((args.bind, args.port), make_handler(state))
    print(f"[INFO] Signage running on http://{args.bind}:{args.port}")
    print("[INFO] Open / in a browser to display the signage screen.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Stopping server...")
    finally:
        stop_event.set()
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
