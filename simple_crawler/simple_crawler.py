#!/usr/bin/env python3
"""Very small BFS crawler for learning purposes."""

from __future__ import annotations

import argparse
from collections import deque
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str]]) -> None:
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.links.add(value)


def normalize_url(base_url: str, href: str) -> str | None:
    url = urljoin(base_url, href)
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    return url


def fetch_html(url: str, timeout: float) -> str:
    req = Request(url, headers={"User-Agent": "SimpleLearningCrawler/1.0"})
    with urlopen(req, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "")
        if "text/html" not in content_type.lower():
            return ""
        return response.read().decode("utf-8", errors="ignore")


def crawl(start_url: str, max_pages: int, max_depth: int, timeout: float) -> list[tuple[int, str]]:
    start_url = normalize_url(start_url, start_url) or start_url
    root_domain = urlparse(start_url).netloc

    queue: deque[tuple[str, int]] = deque([(start_url, 0)])
    visited: set[str] = set()
    results: list[tuple[int, str]] = []

    while queue and len(results) < max_pages:
        url, depth = queue.popleft()
        if url in visited or depth > max_depth:
            continue

        visited.add(url)
        results.append((depth, url))
        print(f"[{len(results):03}] depth={depth} {url}")

        html = fetch_html(url, timeout=timeout)
        if not html:
            continue

        parser = LinkParser()
        parser.feed(html)

        for href in parser.links:
            next_url = normalize_url(url, href)
            if not next_url:
                continue
            if urlparse(next_url).netloc != root_domain:
                continue
            if next_url not in visited:
                queue.append((next_url, depth + 1))

    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple crawler for teaching BFS concepts.")
    parser.add_argument("start_url", help="Starting URL, e.g. https://example.com")
    parser.add_argument("--max-pages", type=int, default=10, help="Maximum number of pages")
    parser.add_argument("--max-depth", type=int, default=1, help="Maximum crawl depth")
    parser.add_argument("--timeout", type=float, default=8.0, help="HTTP timeout in seconds")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    crawl(
        start_url=args.start_url,
        max_pages=args.max_pages,
        max_depth=args.max_depth,
        timeout=args.timeout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
