"""
Ingest reference articles into LibreChat's vectordb via the rag_api `/embed` endpoint.

Usage:
    pip3 install requests beautifulsoup4
    python3 ingest_articles.py
"""

from __future__ import annotations

import os
import re
import time

import requests
from bs4 import BeautifulSoup

RAG_API_URL = "http://localhost:8000"
ENTITY_ID = "article-classifier"
JWT_TOKEN = os.environ.get("JWT_TOKEN", "")
AUTH_HEADERS = {"Authorization": f"Bearer {JWT_TOKEN}"}

ARTICLES = [
    {
        "url": "https://www.channelnewsasia.com/world/iran-war-tehran-airport-israel-strikes-new-wave-5978331",
        "label": "trustworthy",
    },
    {
        "url": "https://www.zaobao.com.sg/news/singapore/story20260307-8695134?ref=today-news-section-card-2",
        "label": "trustworthy",
    },
    {
        "url": "https://mustsharenews.com/jail-caning-psychoactive-vape-substances/",
        "label": "trustworthy",
    },
    {
        "url": "https://www.channelnewsasia.com/style-beauty/syne-studio-kimono-singapore-japan-5949536",
        "label": "trustworthy",
    },
    {
        "url": "https://www.channelnewsasia.com/asia/malaysia-islamic-state-six-youths-arrested-terrorism-sosma-5978561",
        "label": "trustworthy",
    },
    {
        "url": "https://lioncitylife.com/finance/jm-group-limited-就其证券交易暂停提供最新进展/",
        "label": "malicious",
    },
    {
        "url": "https://singapuranow.com/latest-news/克里斯蒂·诺姆在特朗普政府中的新角色是什么？/",
        "label": "malicious",
    },
    {
        "url": "https://voasg.com/jcn-newswire/international-womens-day-why-menopause-may-be-a-missed-cardiovascular-risk-window/",
        "label": "malicious",
    },
    {
        "url": "https://singdaopr.com/latest-news/随着富人撤离中东，私人航班占阿曼机场出港航班/",
        "label": "malicious",
    },
    {
        "url": "http://singaporeinfomap.com/info/Metaverse-War-Begins-Top-Tech-Companies-All-Keep-One-Step-Ahead-Others--2111190029.html",
        "label": "malicious",
    },
]

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/120 Safari/537.36"
    ),
}


def fetch_article_text(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=FETCH_HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ Failed to fetch: {exc}")
        return None

    return soup.get_text(separator=" ", strip=True)[:3000]


def safe_file_id(url: str, label: str, index: int) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]", "-", url)[:60]
    return f"{label}-{index}-{slug}"


def embed_article(file_id: str, text: str) -> requests.Response:
    files = {"file": (f"{file_id}.txt", text.encode("utf-8"), "text/plain")}
    data = {"file_id": file_id, "entity_id": ENTITY_ID}
    return requests.post(
        f"{RAG_API_URL}/embed",
        headers=AUTH_HEADERS,
        files=files,
        data=data,
        timeout=60,
    )


def main() -> None:
    print(f"Ingesting {len(ARTICLES)} reference articles into entity '{ENTITY_ID}'...\n")

    success = 0
    for index, article in enumerate(ARTICLES, start=1):
        url = article["url"]
        label = article["label"]
        print(f"[{index}/{len(ARTICLES)}] [{label.upper()}] {url[:65]}...")

        text = fetch_article_text(url)
        if not text:
            continue
        print(f"  ✓ Fetched ({len(text)} chars)")

        labelled_text = f"LABEL: {label.upper()}\n\n{text}"
        file_id = safe_file_id(url, label, index - 1)

        try:
            resp = embed_article(file_id, labelled_text)
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ Error: {exc}")
            time.sleep(0.5)
            continue

        if resp.status_code in (200, 201):
            print(f"  ✓ Stored (file_id: {file_id[:40]}...)")
            success += 1
        else:
            print(f"  ✗ Failed: {resp.status_code} — {resp.text[:200]}")

        time.sleep(0.5)

    print(f"\n{'=' * 50}")
    print(f"Done! {success}/{len(ARTICLES)} articles ingested.")
    if success == len(ARTICLES):
        print("✅ All articles stored. Ready for the MCP server!")
    else:
        print("⚠️  Some articles failed. Check errors above.")


if __name__ == "__main__":
    main()
