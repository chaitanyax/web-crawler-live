#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
import time
from dataclasses import asdict
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .crawler import crawl


UI_DIR = Path(__file__).resolve().parent / "ui"
DB_PATH = Path(__file__).resolve().parent / "crawl_history.sqlite3"


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS crawl_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_url TEXT NOT NULL,
                max_pages INTEGER NOT NULL,
                max_depth INTEGER NOT NULL,
                delay REAL NOT NULL,
                timeout REAL NOT NULL,
                allow_external INTEGER NOT NULL,
                respect_robots INTEGER NOT NULL,
                user_agent TEXT NOT NULL,
                started_at REAL NOT NULL,
                ended_at REAL,
                status TEXT NOT NULL,
                error_message TEXT,
                visited INTEGER NOT NULL DEFAULT 0,
                queued INTEGER NOT NULL DEFAULT 0,
                errors INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                max_depth_seen INTEGER NOT NULL DEFAULT 0,
                results_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS crawl_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                status INTEGER NOT NULL,
                content_type TEXT NOT NULL,
                depth INTEGER NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY (run_id) REFERENCES crawl_runs(id)
            );
            CREATE INDEX IF NOT EXISTS idx_crawl_results_run_id ON crawl_results(run_id);
            """
        )


def create_run_record(
    start_url: str,
    max_pages: int,
    max_depth: int,
    delay: float,
    timeout: float,
    allow_external: bool,
    respect_robots: bool,
    user_agent: str,
    started_at: float,
) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """
            INSERT INTO crawl_runs (
                start_url,
                max_pages,
                max_depth,
                delay,
                timeout,
                allow_external,
                respect_robots,
                user_agent,
                started_at,
                status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                start_url,
                max_pages,
                max_depth,
                delay,
                timeout,
                int(allow_external),
                int(respect_robots),
                user_agent,
                started_at,
                "running",
            ),
        )
        return int(cur.lastrowid)


def finalize_run_record(
    run_id: int,
    ended_at: float,
    status: str,
    error_message: str | None,
    stats: dict[str, Any],
    results: list[dict[str, Any]],
) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE crawl_runs
            SET ended_at = ?,
                status = ?,
                error_message = ?,
                visited = ?,
                queued = ?,
                errors = ?,
                skipped = ?,
                max_depth_seen = ?,
                results_count = ?
            WHERE id = ?
            """,
            (
                ended_at,
                status,
                error_message,
                int(stats.get("visited", 0)),
                int(stats.get("queued", 0)),
                int(stats.get("errors", 0)),
                int(stats.get("skipped", 0)),
                int(stats.get("max_depth_seen", 0)),
                len(results),
                run_id,
            ),
        )
        conn.execute("DELETE FROM crawl_results WHERE run_id = ?", (run_id,))
        if results:
            created_at = ended_at
            conn.executemany(
                """
                INSERT INTO crawl_results (run_id, url, status, content_type, depth, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        str(row.get("url", "")),
                        int(row.get("status", 0)),
                        str(row.get("content_type", "")),
                        int(row.get("depth", 0)),
                        created_at,
                    )
                    for row in results
                ],
            )


def latest_results_payload() -> dict[str, Any] | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        run_row = conn.execute(
            """
            SELECT
                id,
                start_url,
                started_at,
                ended_at,
                status,
                error_message,
                visited,
                queued,
                errors,
                skipped,
                max_depth_seen,
                results_count
            FROM crawl_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if run_row is None:
            return None

        result_rows = conn.execute(
            """
            SELECT url, status, content_type, depth
            FROM crawl_results
            WHERE run_id = ?
            ORDER BY id ASC
            """,
            (int(run_row["id"]),),
        ).fetchall()

    return {
        "run": dict(run_row),
        "results": [dict(r) for r in result_rows],
    }


class CrawlSession:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.events: list[dict[str, Any]] = []
        self.next_id = 1
        self.running = False
        self.started_at: float | None = None
        self.ended_at: float | None = None
        self.stats = {
            "visited": 0,
            "queued": 0,
            "errors": 0,
            "skipped": 0,
            "max_depth_seen": 0,
        }
        self.last_results: list[dict[str, Any]] = []
        self.current_run_id: int | None = None

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "running": self.running,
                "started_at": self.started_at,
                "ended_at": self.ended_at,
                "stats": dict(self.stats),
                "results_count": len(self.last_results),
                "event_count": len(self.events),
                "current_run_id": self.current_run_id,
            }

    def add_event(self, event: dict[str, Any]) -> None:
        with self.lock:
            event_with_meta = {
                "id": self.next_id,
                "ts": round(time.time(), 3),
                **event,
            }
            self.next_id += 1
            self.events.append(event_with_meta)
            if len(self.events) > 4000:
                self.events = self.events[-2500:]

            event_type = event.get("type")
            depth = int(event.get("depth", 0))
            if depth > self.stats["max_depth_seen"]:
                self.stats["max_depth_seen"] = depth
            if event_type == "visit":
                self.stats["visited"] += 1
            elif event_type == "enqueue":
                self.stats["queued"] += 1
            elif event_type == "error":
                self.stats["errors"] += 1
            elif event_type == "skip":
                self.stats["skipped"] += 1

    def events_after(self, after_id: int) -> list[dict[str, Any]]:
        with self.lock:
            return [e for e in self.events if int(e["id"]) > after_id]

    def reset(self) -> None:
        with self.lock:
            self.events = []
            self.next_id = 1
            self.running = False
            self.started_at = None
            self.ended_at = None
            self.stats = {
                "visited": 0,
                "queued": 0,
                "errors": 0,
                "skipped": 0,
                "max_depth_seen": 0,
            }
            self.last_results = []
            self.current_run_id = None


SESSION = CrawlSession()


def start_crawl(payload: dict[str, Any]) -> tuple[bool, str]:
    start_url = str(payload.get("start_url", "")).strip()
    if not start_url:
        return False, "start_url is required"

    max_pages = int(payload.get("max_pages", 40))
    max_depth = int(payload.get("max_depth", 2))
    delay = float(payload.get("delay", 0.2))
    timeout = float(payload.get("timeout", 10.0))
    allow_external = bool(payload.get("allow_external", False))
    respect_robots = bool(payload.get("respect_robots", True))
    user_agent = str(payload.get("user_agent", "CrawlerUI/1.0"))
    started_at = time.time()

    with SESSION.lock:
        if SESSION.running:
            return False, "crawl already running"
        SESSION.events = []
        SESSION.next_id = 1
        SESSION.running = True
        SESSION.started_at = started_at
        SESSION.ended_at = None
        SESSION.stats = {
            "visited": 0,
            "queued": 0,
            "errors": 0,
            "skipped": 0,
            "max_depth_seen": 0,
        }
        SESSION.last_results = []
        SESSION.current_run_id = None

    try:
        run_id = create_run_record(
            start_url=start_url,
            max_pages=max_pages,
            max_depth=max_depth,
            delay=delay,
            timeout=timeout,
            allow_external=allow_external,
            respect_robots=respect_robots,
            user_agent=user_agent,
            started_at=started_at,
        )
    except Exception as exc:  # noqa: BLE001
        with SESSION.lock:
            SESSION.running = False
            SESSION.started_at = None
            SESSION.ended_at = None
        return False, f"could not initialize sqlite record: {exc}"

    with SESSION.lock:
        SESSION.current_run_id = run_id

    def worker() -> None:
        run_status = "completed"
        run_error: str | None = None
        try:
            SESSION.add_event({"type": "start", "url": start_url, "depth": 0})
            results = crawl(
                start_url=start_url,
                max_pages=max_pages,
                max_depth=max_depth,
                delay=delay,
                timeout=timeout,
                same_host_only=not allow_external,
                user_agent=user_agent,
                respect_robots=respect_robots,
                on_event=SESSION.add_event,
            )
            with SESSION.lock:
                SESSION.last_results = [asdict(r) for r in results]
        except Exception as exc:  # noqa: BLE001
            run_status = "failed"
            run_error = str(exc)
            SESSION.add_event({"type": "fatal", "error": str(exc), "depth": 0})
        finally:
            with SESSION.lock:
                SESSION.running = False
                SESSION.ended_at = time.time()
                ended_at = SESSION.ended_at
                final_stats = dict(SESSION.stats)
                final_results = list(SESSION.last_results)
            if ended_at is None:
                ended_at = time.time()
            try:
                finalize_run_record(
                    run_id=run_id,
                    ended_at=ended_at,
                    status=run_status,
                    error_message=run_error,
                    stats=final_stats,
                    results=final_results,
                )
            except Exception as exc:  # noqa: BLE001
                SESSION.add_event({"type": "fatal", "error": f"sqlite finalize failed: {exc}", "depth": 0})
            SESSION.add_event({"type": "complete", "depth": 0, "count": len(SESSION.last_results)})

    threading.Thread(target=worker, daemon=True).start()
    return True, "crawl started"


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(UI_DIR), **kwargs)

    def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _parse_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            decoded = json.loads(raw.decode("utf-8"))
            return decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/events":
            query = parse_qs(parsed.query)
            after = int(query.get("after", ["0"])[0])
            events = SESSION.events_after(after)
            self._send_json({"ok": True, "events": events, "state": SESSION.snapshot()})
            return
        if parsed.path == "/api/state":
            self._send_json({"ok": True, "state": SESSION.snapshot()})
            return
        if parsed.path == "/api/results/latest":
            payload = latest_results_payload()
            if payload is None:
                self._send_json({"ok": True, "run": None, "results": []})
                return
            self._send_json({"ok": True, **payload})
            return
        if parsed.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/start":
            payload = self._parse_json_body()
            ok, message = start_crawl(payload)
            status = HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST
            self._send_json({"ok": ok, "message": message, "state": SESSION.snapshot()}, status=status)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args: Any) -> None:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the web crawler live UI server.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", default=8000, type=int, help="Port to bind")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    init_db()
    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    print(f"Crawler UI running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
