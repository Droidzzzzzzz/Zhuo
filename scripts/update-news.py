#!/usr/bin/env python3
"""
Fetch latest news from Google News RSS for tracked companies,
merge with existing archive, and write to news-data.json.

Usage:
  python3 scripts/update-news.py

Designed to run locally or via GitHub Actions on a schedule.
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from xml.etree import ElementTree

# ── Configuration ────────────────────────────────────────────────

COMPANIES = [
    {"name": "Novo Nordisk",         "query": "Novo Nordisk",                    "color": "var(--purple)", "logo": "logos/novo-nordisk.png"},
    {"name": "Hyundai E&C",          "query": "Hyundai Engineering Construction","color": "var(--coral)",  "logo": "logos/hyundai-enc.png"},
    {"name": "Kiwoom Securities",    "query": "Kiwoom Securities",               "color": "var(--teal)",   "logo": "logos/kiwoom-securities.png"},
    {"name": "Woori Financial Group", "query": "Woori Financial Group",           "color": "var(--amber)",  "logo": "logos/woori-financial.png"},
]

ALLOWED_SOURCES = [
    "Nikkei Asia",
    "Financial Times",
    "Bloomberg",
    "South China Morning Post",
    "CHOSUNBIZ",
    "The Korea Times",
    "The Korea Herald",
    "Reuters",
    "The Japan Times",
    "Yonhap News Agency",
    "CNBC",
    "Barron's",
    "WSJ",
]

# Path to the JSON data file (relative to repo root)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_FILE = os.path.join(REPO_ROOT, "news-data.json")

# How many items to keep per company from each fetch
MAX_ITEMS_PER_FETCH = 20

# Delay between API calls to avoid rate limiting (seconds)
FETCH_DELAY = 2

# ── Helpers ──────────────────────────────────────────────────────

def google_news_rss_url(query):
    """Build a Google News RSS search URL."""
    params = urllib.parse.urlencode({
        "q": query,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    })
    return f"https://news.google.com/rss/search?{params}"


def fetch_rss(url):
    """Fetch and parse an RSS feed, returning a list of item dicts."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; NewsPulseBot/1.0)"
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        xml_bytes = resp.read()

    root = ElementTree.fromstring(xml_bytes)
    items = []
    for item_el in root.iter("item"):
        title = (item_el.findtext("title") or "").strip()
        link = (item_el.findtext("link") or "").strip()
        pub_date = (item_el.findtext("pubDate") or "").strip()
        source_el = item_el.find("source")
        source = source_el.text.strip() if source_el is not None and source_el.text else ""

        items.append({
            "title": title,
            "link": link,
            "pubDate": pub_date,
            "source": source,
        })

    return items


def parse_date(date_str):
    """Try to parse an RSS date string into a datetime object."""
    # RSS dates are typically RFC 822: "Thu, 27 Feb 2026 10:30:00 GMT"
    formats = [
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def is_from_allowed_source(item):
    """Check if an item's source or title mentions an allowed outlet."""
    title_lower = item["title"].lower()
    source_lower = item["source"].lower()
    for src in ALLOWED_SOURCES:
        src_lower = src.lower()
        if src_lower in title_lower or src_lower in source_lower:
            return True
    return False


def normalize_item(item):
    """Return a clean dict for JSON storage."""
    dt = parse_date(item["pubDate"])
    iso_date = dt.isoformat() if dt else item["pubDate"]

    # Clean title: Google News always appends " - Source Name"
    title = item["title"]
    source = item["source"]

    # Google News format: "Article Title - Source Name"
    # Some sources (e.g. CHOSUNBIZ) produce: "Title - CHOSUNBIZ - Chosunbiz"
    # Strip all trailing " - <source>" segments
    while " - " in title:
        parts = title.rsplit(" - ", 1)
        trailing = parts[1].strip()
        # Check if the trailing segment is a known source (case-insensitive)
        is_source = any(
            trailing.lower() == src.lower()
            for src in ALLOWED_SOURCES
        )
        if is_source or (source and trailing.lower() == source.lower()):
            if not source:
                source = trailing
            title = parts[0].strip()
        else:
            break

    return {
        "title": title,
        "link": item["link"],
        "pubDate": iso_date,
        "source": source,
    }


def load_existing_data():
    """Load the existing news-data.json or return empty structure."""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "lastUpdated": "",
        "companies": {},
    }


def save_data(data):
    """Write data to news-data.json."""
    data["lastUpdated"] = datetime.now(timezone.utc).isoformat()
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved to {DATA_FILE}")


def merge_items(existing, new_items):
    """Merge new items into existing list, deduplicating by link."""
    seen_links = {item["link"] for item in existing}
    added = 0
    for item in new_items:
        if item["link"] not in seen_links:
            existing.append(item)
            seen_links.add(item["link"])
            added += 1
    # Sort by date descending
    existing.sort(key=lambda x: x.get("pubDate", ""), reverse=True)
    return existing, added


# ── Main ─────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("News Pulse Updater")
    print("=" * 50)

    data = load_existing_data()

    # Store company metadata
    if "companyMeta" not in data:
        data["companyMeta"] = {}
    for co in COMPANIES:
        data["companyMeta"][co["name"]] = {
            "color": co["color"],
            "query": co["query"],
            "logo": co.get("logo", ""),
        }

    if "companies" not in data:
        data["companies"] = {}

    total_added = 0

    for i, company in enumerate(COMPANIES):
        name = company["name"]
        print(f"\nFetching news for: {name}")

        try:
            rss_url = google_news_rss_url(company["query"])
            raw_items = fetch_rss(rss_url)
            print(f"  Raw items from RSS: {len(raw_items)}")

            # Filter by allowed sources
            filtered = [it for it in raw_items if is_from_allowed_source(it)]
            print(f"  After source filter: {len(filtered)}")

            # Normalize
            normalized = [normalize_item(it) for it in filtered[:MAX_ITEMS_PER_FETCH]]

            # Merge with existing
            existing = data["companies"].get(name, [])
            merged, added = merge_items(existing, normalized)
            data["companies"][name] = merged
            total_added += added
            print(f"  New items added: {added} (total archived: {len(merged)})")

        except Exception as e:
            print(f"  ERROR fetching {name}: {e}")

        # Delay between requests to be polite
        if i < len(COMPANIES) - 1:
            time.sleep(FETCH_DELAY)

    save_data(data)
    print(f"\nDone! Added {total_added} new items total.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
