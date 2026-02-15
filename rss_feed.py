#!/usr/bin/env python3
"""Shared RSS/Atom feed helpers."""

import re
import ssl
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import List, Optional, Tuple
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
RSS_NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}
WS_RE = re.compile(r"\s+")
TAG_RE = re.compile(r"<[^>]+>")
USER_AGENT = "newsfeed-rss-client/1.0 (+https://example.invalid)"


@dataclass
class NewsEntry:
    source: str
    title: str
    link: str
    published: Optional[str]
    summary: Optional[str] = None
    author: Optional[str] = None


def clean_text(value: Optional[str]) -> str:
    return WS_RE.sub(" ", value or "").strip()


def clean_html_text(value: Optional[str]) -> str:
    text = TAG_RE.sub(" ", value or "")
    return clean_text(unescape(text))


def parse_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None


def sort_datetime(value: Optional[datetime]) -> datetime:
    floor = datetime.min.replace(tzinfo=timezone.utc)
    if value is None:
        return floor
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def format_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return sort_datetime(value).isoformat().replace("+00:00", "Z")


def detect_source(url: str, root: ET.Element) -> str:
    rss_title = clean_text(root.findtext("./channel/title"))
    if rss_title:
        return rss_title

    rdf_title = clean_text(root.findtext("./{*}channel/{*}title"))
    if rdf_title:
        return rdf_title

    atom_title = clean_text(root.findtext("./atom:title", default="", namespaces=ATOM_NS))
    if atom_title:
        return atom_title

    return urlparse(url).netloc or url


def parse_rss(root: ET.Element, source: str) -> List[Tuple[Optional[datetime], NewsEntry]]:
    items: List[Tuple[Optional[datetime], NewsEntry]] = []
    for item in root.findall("./channel/item"):
        title = clean_text(item.findtext("title"))
        link = clean_text(item.findtext("link"))
        if not title or not link:
            continue

        published_dt = parse_datetime(clean_text(item.findtext("pubDate")))
        summary = clean_html_text(item.findtext("description"))
        if not summary:
            summary = clean_html_text(item.findtext("content:encoded", default="", namespaces=RSS_NS))
        author = clean_text(item.findtext("author"))
        if not author:
            author = clean_text(item.findtext("dc:creator", default="", namespaces=RSS_NS))
        items.append(
            (
                published_dt,
                NewsEntry(
                    source=source,
                    title=title,
                    link=link,
                    published=format_datetime(published_dt),
                    summary=summary or None,
                    author=author or None,
                ),
            )
        )
    return items


def parse_atom(root: ET.Element, source: str) -> List[Tuple[Optional[datetime], NewsEntry]]:
    items: List[Tuple[Optional[datetime], NewsEntry]] = []
    for entry in root.findall("./atom:entry", ATOM_NS):
        title = clean_text(entry.findtext("atom:title", default="", namespaces=ATOM_NS))
        if not title:
            continue

        link = ""
        for link_node in entry.findall("atom:link", ATOM_NS):
            href = clean_text(link_node.attrib.get("href", ""))
            if not href:
                continue
            rel = link_node.attrib.get("rel", "alternate")
            if rel == "alternate":
                link = href
                break
            if not link:
                link = href

        if not link:
            continue

        published_raw = clean_text(entry.findtext("atom:published", default="", namespaces=ATOM_NS))
        if not published_raw:
            published_raw = clean_text(entry.findtext("atom:updated", default="", namespaces=ATOM_NS))

        summary = clean_html_text(entry.findtext("atom:summary", default="", namespaces=ATOM_NS))
        if not summary:
            summary = clean_html_text(entry.findtext("atom:content", default="", namespaces=ATOM_NS))
        author = clean_text(entry.findtext("atom:author/atom:name", default="", namespaces=ATOM_NS))

        published_dt = parse_datetime(published_raw)
        items.append(
            (
                published_dt,
                NewsEntry(
                    source=source,
                    title=title,
                    link=link,
                    published=format_datetime(published_dt),
                    summary=summary or None,
                    author=author or None,
                ),
            )
        )
    return items


def parse_rdf(root: ET.Element, source: str) -> List[Tuple[Optional[datetime], NewsEntry]]:
    items: List[Tuple[Optional[datetime], NewsEntry]] = []
    for item in root.findall("./{*}item"):
        title = clean_text(item.findtext("{*}title"))
        link = clean_text(item.findtext("{*}link"))
        if not title or not link:
            continue

        published_raw = clean_text(item.findtext("{*}date"))
        if not published_raw:
            published_raw = clean_text(item.findtext("dc:date", default="", namespaces=RSS_NS))

        summary = clean_html_text(item.findtext("{*}description"))
        author = clean_text(item.findtext("dc:creator", default="", namespaces=RSS_NS))
        published_dt = parse_datetime(published_raw)
        items.append(
            (
                published_dt,
                NewsEntry(
                    source=source,
                    title=title,
                    link=link,
                    published=format_datetime(published_dt),
                    summary=summary or None,
                    author=author or None,
                ),
            )
        )
    return items


def parse_feed(xml_text: str, url: str) -> List[Tuple[Optional[datetime], NewsEntry]]:
    root = ET.fromstring(xml_text)
    source = detect_source(url, root)
    tag = root.tag.lower()

    if tag.endswith("rss") or root.find("./channel") is not None:
        return parse_rss(root, source)
    if tag.endswith("feed"):
        return parse_atom(root, source)
    if tag.endswith("rdf") or tag.endswith("rdf}"):
        return parse_rdf(root, source)
    raise ValueError("Unsupported feed format (expected RSS or Atom).")


def fetch_feed(url: str, timeout: float, verify_ssl: bool = True) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    context = ssl.create_default_context() if verify_ssl else ssl._create_unverified_context()
    with urlopen(req, timeout=timeout, context=context) as resp:
        body = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"

    try:
        return body.decode(charset)
    except LookupError:
        return body.decode("utf-8", errors="replace")


def collect_entries(
    urls: List[str],
    timeout: float = 15.0,
    keyword: Optional[str] = None,
    limit: int = 10,
    verify_ssl: bool = True,
) -> Tuple[List[NewsEntry], List[str]]:
    all_items: List[Tuple[Optional[datetime], NewsEntry]] = []
    errors: List[str] = []

    for url in urls:
        try:
            xml_text = fetch_feed(url, timeout, verify_ssl=verify_ssl)
            all_items.extend(parse_feed(xml_text, url))
        except Exception as exc:  # pylint: disable=broad-except
            errors.append(f"Failed to read {url}: {exc}")

    if keyword:
        needle = keyword.casefold()
        all_items = [
            item
            for item in all_items
            if (
                needle in item[1].title.casefold()
                or needle in item[1].source.casefold()
                or needle in (item[1].summary or "").casefold()
                or needle in (item[1].author or "").casefold()
            )
        ]

    all_items.sort(key=lambda item: sort_datetime(item[0]), reverse=True)
    limited = [item[1] for item in all_items[: max(limit, 0)]]
    return limited, errors
