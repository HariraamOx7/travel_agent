"""Collect candidate facts from approved official Indian attraction pages.

Add {"name": ..., "url": ...} records to data/official_place_sources.json.
Results go to a review queue; nothing updates the approved profiles by itself.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import scrapy
from scrapy.crawler import CrawlerProcess

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "data" / "official_place_sources.json"
REVIEW = ROOT / "data" / "official_place_review.json"
_HINT = re.compile(r"(?:ticket|entry fee|admission|timing|opening hours|closed|photograph|permit|season)", re.I)


class OfficialPlacesSpider(scrapy.Spider):
    name = "official_india_places"
    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "DOWNLOAD_DELAY": 3,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "USER_AGENT": "India travel planner academic data review/1.0",
        "LOG_LEVEL": "WARNING",
    }

    def __init__(self, rows, results, **kwargs):
        super().__init__(**kwargs)
        self.rows = rows
        self.results = results

    def start_requests(self):
        for row in self.rows:
            url = row.get("url", "")
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.hostname or not row.get("name"):
                continue
            yield scrapy.Request(url, callback=self.parse,
                                 cb_kwargs={"name": row["name"]})

    def parse(self, response, name):
        paragraphs = response.css("main p::text, article p::text, main li::text, article li::text").getall()
        if not paragraphs:
            paragraphs = response.css("p::text, li::text").getall()
        snippets = [" ".join(part.split())[:400] for part in paragraphs
                    if _HINT.search(part)]
        self.results.append({"name": name, "source_url": response.url,
                             "last_checked": date.today().isoformat(),
                             "candidate_snippets": snippets[:30],
                             "status": "needs_review"})


def main():
    rows = json.loads(SOURCES.read_text(encoding="utf-8"))
    results = []
    process = CrawlerProcess()
    process.crawl(OfficialPlacesSpider, rows=rows, results=results)
    process.start()
    REVIEW.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                      encoding="utf-8")
    print(f"Saved {len(results)} pages for review to {REVIEW}")


if __name__ == "__main__":
    main()
