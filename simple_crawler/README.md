# Simple Crawler (Teaching Version)

This folder contains a very basic crawler to teach core ideas before using the full app.

## Learning Goals

- understand BFS crawling with a queue
- track `visited` URLs
- control crawl using `max-pages` and `max-depth`
- extract links from HTML

## Run

From project root:

```bash
python3 simple_crawler/simple_crawler.py https://example.com --max-pages 10 --max-depth 1
```

## What It Does

- visits URLs in BFS order
- stays in the same domain as the start URL
- prints each visited URL and depth
- skips non-HTML pages

## What It Does Not Do (By Design)

- no UI
- no database
- no event API
- no robots.txt support
- no JSON/CSV export

Use this as a first step, then move to the full crawler in the project root.
