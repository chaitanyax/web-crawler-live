#!/usr/bin/env python3
"""
Simple web crawler (BFS) using only Python standard library.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable, Deque, Iterable, Set, Tuple
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class CrawlResult:
    url: str
    status: int
    content_type: str
    depth: int


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: Set[str] = set()

    def handle_starttag(self, tag: str, attrs: Iterable[Tuple[str, str]]) -> None:
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.links.add(value)


def normalize_url(base_url: str, href: str) -> str | None:
    absolute = urljoin(base_url, href)
    absolute, _ = urldefrag(absolute)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return None
    return absolute


def same_domain(url: str, root_netloc: str) -> bool:
    return urlparse(url).netloc == root_netloc


def fetch_page(url: str, timeout: float, user_agent: str) -> tuple[int, str, str]:
    request = Request(url, headers={"User-Agent": user_agent})
    with urlopen(request, timeout=timeout) as response:
        status = getattr(response, "status", 200)
        content_type = response.headers.get("Content-Type", "")
        body = response.read().decode("utf-8", errors="ignore")
        return status, content_type, body


def load_robots_parser(url: str, timeout: float, user_agent: str) -> RobotFileParser | None:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    request = Request(robots_url, headers={"User-Agent": user_agent})
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="ignore")
        parser.parse(body.splitlines())
        return parser
    except Exception:
        # If robots.txt is unavailable, crawler falls back to permissive behavior.
        return None


def is_allowed_by_robots(
    url: str,
    user_agent: str,
    timeout: float,
    parser_cache: dict[str, RobotFileParser | None],
) -> bool:
    netloc = urlparse(url).netloc
    if netloc not in parser_cache:
        parser_cache[netloc] = load_robots_parser(url=url, timeout=timeout, user_agent=user_agent)

    parser = parser_cache[netloc]
    if parser is None:
        return True
    return parser.can_fetch(user_agent, url)


def crawl(
    start_url: str,
    max_pages: int = 30,
    max_depth: int = 2,
    delay: float = 0.2,
    timeout: float = 10.0,
    same_host_only: bool = True,
    user_agent: str = "SimpleCrawler/1.0",
    respect_robots: bool = True,
    on_event: Callable[[dict], None] | None = None,
) -> list[CrawlResult]:
    start_url = normalize_url(start_url, start_url) or start_url
    root_netloc = urlparse(start_url).netloc

    queue: Deque[tuple[str, int]] = collections.deque([(start_url, 0)])
    visited: Set[str] = set()
    results: list[CrawlResult] = []
    robots_cache: dict[str, RobotFileParser | None] = {}

    while queue and len(results) < max_pages:
        url, depth = queue.popleft()
        if url in visited or depth > max_depth:
            continue
        visited.add(url)

        if respect_robots and not is_allowed_by_robots(
            url=url, user_agent=user_agent, timeout=timeout, parser_cache=robots_cache
        ):
            print(f"[SKIP] robots.txt disallows {url}", file=sys.stderr)
            if on_event:
                on_event({"type": "skip", "url": url, "depth": depth, "reason": "robots"})
            continue

        try:
            status, content_type, body = fetch_page(url, timeout=timeout, user_agent=user_agent)
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR] {url} -> {exc}", file=sys.stderr)
            if on_event:
                on_event({"type": "error", "url": url, "depth": depth, "error": str(exc)})
            continue

        results.append(CrawlResult(url=url, status=status, content_type=content_type, depth=depth))
        print(f"[{len(results):03}] depth={depth} status={status} {url}")
        if on_event:
            on_event(
                {
                    "type": "visit",
                    "url": url,
                    "depth": depth,
                    "status": status,
                    "content_type": content_type,
                    "count": len(results),
                }
            )

        if "text/html" not in content_type.lower():
            continue

        parser = LinkExtractor()
        parser.feed(body)
        for href in parser.links:
            normalized = normalize_url(url, href)
            if not normalized:
                continue
            if same_host_only and not same_domain(normalized, root_netloc):
                continue
            if normalized not in visited:
                queue.append((normalized, depth + 1))
                if on_event:
                    on_event({"type": "enqueue", "url": normalized, "depth": depth + 1})

        if delay > 0:
            time.sleep(delay)

    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A simple BFS web crawler.")
    parser.add_argument("start_url", help="Starting URL (e.g. https://example.com)")
    parser.add_argument("--max-pages", type=int, default=30, help="Maximum pages to crawl")
    parser.add_argument("--max-depth", type=int, default=2, help="Maximum link depth")
    parser.add_argument("--delay", type=float, default=0.2, help="Delay between requests (seconds)")
    parser.add_argument("--timeout", type=float, default=10.0, help="Request timeout (seconds)")
    parser.add_argument(
        "--allow-external",
        action="store_true",
        help="If set, crawl links outside the starting domain",
    )
    parser.add_argument(
        "--user-agent",
        default="SimpleCrawler/1.0",
        help="User-Agent header",
    )
    parser.add_argument(
        "--ignore-robots",
        action="store_true",
        help="If set, ignore robots.txt rules",
    )
    parser.add_argument("--json-out", help="Write crawl results to this JSON file")
    parser.add_argument("--csv-out", help="Write crawl results to this CSV file")
    return parser.parse_args()


def write_json(path: str, results: list[CrawlResult]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([r.__dict__ for r in results], f, indent=2)


def write_csv(path: str, results: list[CrawlResult]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["url", "status", "content_type", "depth"])
        writer.writeheader()
        for row in results:
            writer.writerow(row.__dict__)


def main() -> int:
    args = parse_args()
    results = crawl(
        start_url=args.start_url,
        max_pages=args.max_pages,
        max_depth=args.max_depth,
        delay=args.delay,
        timeout=args.timeout,
        same_host_only=not args.allow_external,
        user_agent=args.user_agent,
        respect_robots=not args.ignore_robots,
    )
    if args.json_out:
        write_json(args.json_out, results)
        print(f"[OUT] Wrote JSON to {args.json_out}")
    if args.csv_out:
        write_csv(args.csv_out, results)
        print(f"[OUT] Wrote CSV to {args.csv_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
