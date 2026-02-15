#!/usr/bin/env python3
"""Fetch RSS/Atom feeds and print recent news entries."""

import argparse
import json
import sys
from dataclasses import asdict

from rss_feed import collect_entries


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch headlines from RSS/Atom feeds.")
    parser.add_argument("urls", nargs="+", help="RSS or Atom feed URLs")
    parser.add_argument("--limit", type=int, default=10, help="Max number of entries to output")
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout (seconds)")
    parser.add_argument("--keyword", help="Filter entries by keyword (case-insensitive)")
    parser.add_argument("--insecure", action="store_true", help="Disable SSL certificate verification")
    parser.add_argument("--json", action="store_true", help="Print output as JSON")
    args = parser.parse_args()

    entries, errors = collect_entries(
        urls=args.urls,
        timeout=args.timeout,
        keyword=args.keyword,
        limit=args.limit,
        verify_ssl=not args.insecure,
    )

    for message in errors:
        print(f"[WARN] {message}", file=sys.stderr)

    if not entries:
        print("No feed entries found.", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([asdict(item) for item in entries], ensure_ascii=False, indent=2))
        return 0

    for item in entries:
        print(f"- [{item.source}] {item.title}")
        print(f"  {item.link}")
        print(f"  {item.published or 'unknown-time'}")
        if item.author:
            print(f"  by: {item.author}")
        if item.summary:
            print(f"  summary: {item.summary}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
