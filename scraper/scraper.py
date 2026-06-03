import os
import time
import logging
from datetime import datetime, timezone

import requests
from opensearchpy import OpenSearch, helpers

OPENSEARCH_URL = os.getenv("OPENSEARCH_URL", "http://opensearch:9200")
JIKAN_BASE = "https://api.jikan.moe/v4"
INDEX = "manga"
MAX_PAGES = int(os.getenv("MAX_PAGES", "200"))
REQUEST_SLEEP = 0.4  # stay under Jikan's 3 req/sec limit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

client = OpenSearch(hosts=[OPENSEARCH_URL])

INDEX_MAPPINGS = {
    "mappings": {
        "properties": {
            "mal_id":          {"type": "integer"},
            "title":           {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "title_english":   {"type": "text"},
            "synopsis":        {"type": "text"},
            "score":           {"type": "float"},
            "scored_by":       {"type": "integer"},
            "cover_url":       {"type": "keyword", "index": False},
            "large_cover_url": {"type": "keyword", "index": False},
            "genres":          {"type": "keyword"},
            "status":          {"type": "keyword"},
            "volumes":         {"type": "integer"},
            "chapters":        {"type": "integer"},
            "authors":         {"type": "keyword"},
            "indexed_at":      {"type": "date"},
        }
    }
}


def ensure_index():
    if not client.indices.exists(INDEX):
        client.indices.create(INDEX, body=INDEX_MAPPINGS)
        log.info("Created index '%s'", INDEX)


def fetch_page(page: int) -> dict:
    url = f"{JIKAN_BASE}/manga"
    params = {"page": page, "limit": 25, "order_by": "score", "sort": "desc"}
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def parse_manga(item: dict) -> dict:
    images = item.get("images", {}).get("jpg", {})
    return {
        "mal_id":          item["mal_id"],
        "title":           item.get("title", ""),
        "title_english":   item.get("title_english") or "",
        "synopsis":        item.get("synopsis") or "",
        "score":           item.get("score"),
        "scored_by":       item.get("scored_by"),
        "cover_url":       images.get("image_url", ""),
        "large_cover_url": images.get("large_image_url", ""),
        "genres":          [g["name"] for g in item.get("genres", [])],
        "status":          item.get("status", ""),
        "volumes":         item.get("volumes"),
        "chapters":        item.get("chapters"),
        "authors":         [a["name"] for a in item.get("authors", [])],
        "indexed_at":      datetime.now(timezone.utc).isoformat(),
    }


def bulk_index(docs: list) -> int:
    actions = [
        {"_index": INDEX, "_id": doc["mal_id"], "_source": doc}
        for doc in docs
    ]
    success, errors = helpers.bulk(client, actions, raise_on_error=False)
    if errors:
        log.warning("Bulk errors (first 3): %s", errors[:3])
    return success


def run():
    ensure_index()
    page = 1
    total = 0

    while page <= MAX_PAGES:
        try:
            data = fetch_page(page)
        except requests.HTTPError as e:
            if e.response.status_code == 429:
                log.warning("Rate limited — sleeping 60s")
                time.sleep(60)
                continue
            log.error("HTTP error on page %d: %s", page, e)
            break
        except Exception as e:
            log.error("Error on page %d: %s", page, e)
            break

        items = data.get("data", [])
        if not items:
            log.info("Empty page — stopping")
            break

        docs = [parse_manga(item) for item in items]
        indexed = bulk_index(docs)
        total += indexed

        pagination = data.get("pagination", {})
        last_page = pagination.get("last_visible_page", 1)
        has_next = pagination.get("has_next_page", False)

        log.info(
            "Page %d/%d — indexed %d (total so far: %d)",
            page, min(last_page, MAX_PAGES), indexed, total,
        )

        if not has_next or page >= min(last_page, MAX_PAGES):
            break

        page += 1
        time.sleep(REQUEST_SLEEP)

    log.info("Scrape complete. Total indexed: %d", total)


if __name__ == "__main__":
    run()
