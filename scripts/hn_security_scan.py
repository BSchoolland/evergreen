#!/usr/bin/env python3
"""Fetch recent HackerNews posts matching security queries, with optional full content fetching."""

import concurrent.futures
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

QUERIES = [
    "vulnerability",
    "CVE",
    "security exploit",
    "RCE",
    "zero-day",
    "data breach",
    "npm malware",
    "supply chain attack",
]


class HTMLToText(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = False
        if tag in ("p", "br", "div", "h1", "h2", "h3", "h4", "li", "tr"):
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def get_text(self):
        text = "".join(self._parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def fetch_hn_posts(hours=24, max_hits=50):
    cutoff = int(time.time()) - (hours * 3600)
    seen_ids = set()
    all_hits = []

    for query in QUERIES:
        url = (
            f"https://hn.algolia.com/api/v1/search_by_date?"
            f"tags=story&query={urllib.parse.quote(query)}"
            f"&numericFilters=created_at_i>{cutoff}&hitsPerPage={max_hits}"
        )
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
            for hit in data.get("hits", []):
                oid = hit["objectID"]
                if oid not in seen_ids:
                    seen_ids.add(oid)
                    all_hits.append({
                        "id": oid,
                        "title": hit.get("title", ""),
                        "url": hit.get("url", ""),
                        "points": hit.get("points", 0),
                        "comments": hit.get("num_comments", 0),
                        "created_at": hit.get("created_at", ""),
                        "hn_url": f"https://news.ycombinator.com/item?id={oid}",
                    })
        except Exception as e:
            print(f"Warning: query '{query}' failed: {e}", file=sys.stderr)

    all_hits.sort(key=lambda h: h["points"] or 0, reverse=True)
    return all_hits


def fetch_hn_comments(post_id, max_comments=15):
    """Fetch top-level comments from the HN API for a given post."""
    url = f"https://hn.algolia.com/api/v1/items/{post_id}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        comments = []
        for child in (data.get("children") or [])[:max_comments]:
            text = child.get("text", "")
            if text:
                parser = HTMLToText()
                parser.feed(text)
                comments.append(parser.get_text())
        return comments
    except Exception as e:
        print(f"Warning: failed to fetch comments for {post_id}: {e}", file=sys.stderr)
        return []


def fetch_article_text(url, max_chars=8000):
    """Fetch a URL and extract visible text, truncated to max_chars."""
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; EvergreenBot/1.0)"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "html" not in content_type and "text" not in content_type:
                return f"[Non-HTML content: {content_type}]"
            raw = resp.read(200_000).decode("utf-8", errors="replace")
        parser = HTMLToText()
        parser.feed(raw)
        text = parser.get_text()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n[truncated]"
        return text
    except Exception as e:
        return f"[Failed to fetch: {e}]"


def fetch_full_context(post, include_comments=True):
    """Fetch article text and HN comments for a post. Returns enriched post dict."""
    post = dict(post)
    post["article_text"] = fetch_article_text(post.get("url", ""))
    if include_comments:
        post["hn_comments"] = fetch_hn_comments(post["id"])
    return post


def fetch_full_contexts_parallel(posts, max_workers=8):
    """Fetch full context for multiple posts in parallel."""
    enriched = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_full_context, p): p for p in posts}
        for future in concurrent.futures.as_completed(futures):
            try:
                enriched.append(future.result())
            except Exception as e:
                post = futures[future]
                print(f"Warning: failed to enrich post {post['id']}: {e}", file=sys.stderr)
                enriched.append(post)
    return enriched


def get_tracked_urls(project_id=None):
    """Return set of source_urls already in the security_alerts table for the project."""
    try:
        from evergreen.db import current_project_id, get_connection
        if project_id is None:
            project_id = current_project_id() or 1
        conn = get_connection()
        rows = conn.execute(
            "SELECT source_url FROM security_alerts WHERE source_url IS NOT NULL AND project_id = ?",
            (project_id,),
        ).fetchall()
        conn.close()
        return {row[0] for row in rows}
    except Exception:
        return set()


def main():
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    fetch_ids = sys.argv[2:] if len(sys.argv) > 2 else []

    print(f"Fetching HN security posts from the last {hours} hours...")
    posts = fetch_hn_posts(hours=hours)
    print(f"Found {len(posts)} unique posts.\n")

    tracked = get_tracked_urls()
    if tracked:
        print(f"({len(tracked)} HN URLs already tracked in DB)\n")

    if fetch_ids:
        to_fetch = [p for p in posts if p["id"] in set(fetch_ids)]
        print(f"Fetching full context for {len(to_fetch)} selected posts...\n")
        enriched = fetch_full_contexts_parallel(to_fetch)
        for post in enriched:
            tag = " [TRACKED]" if post["hn_url"] in tracked else ""
            print(f"={'=' * 79}")
            print(f"Title: {post['title']}{tag}")
            print(f"HN: {post['hn_url']}")
            print(f"URL: {post.get('url', 'N/A')}")
            print(f"Points: {post.get('points', 0)} | Comments: {post.get('comments', 0)}")
            print(f"-{'-' * 79}")
            article = post.get("article_text", "")
            if article:
                print(f"ARTICLE:\n{article}\n")
            comments = post.get("hn_comments", [])
            if comments:
                print(f"TOP HN COMMENTS ({len(comments)}):")
                for i, c in enumerate(comments, 1):
                    print(f"  [{i}] {c[:500]}")
                    print()
            print()
        json.dump(enriched, open("/tmp/hn_security_enriched.json", "w"), indent=2)
        print(f"Wrote {len(enriched)} enriched posts to /tmp/hn_security_enriched.json")
    else:
        for i, post in enumerate(posts, 1):
            tag = " [TRACKED]" if post["hn_url"] in tracked else ""
            print(f"{i:3}. [{post['points'] or 0:4}pts] {post['title']}{tag}")
            print(f"     HN: {post['hn_url']}")
            if post["url"]:
                print(f"     URL: {post['url']}")
            print()
        json.dump(posts, open("/tmp/hn_security_posts.json", "w"), indent=2)
        print(f"Wrote {len(posts)} posts to /tmp/hn_security_posts.json")


if __name__ == "__main__":
    main()
