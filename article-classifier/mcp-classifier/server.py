"""MCP server for the article-classifier proof of concept."""

from __future__ import annotations

import os
import re

import requests
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

RAG_API_URL = os.environ.get("RAG_API_URL", "http://rag_api:8000")
JWT_TOKEN = os.environ.get("JWT_TOKEN", "")
ENTITY_ID = "article-classifier"
TOP_K = 5

AUTH_HEADERS = {"Authorization": f"Bearer {JWT_TOKEN}"}
FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/120 Safari/537.36"
    ),
}

mcp = FastMCP(
    "article-classifier",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def fetch_article_text(url: str) -> str:
    resp = requests.get(url, headers=FETCH_HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def query_similar(text: str) -> list[dict]:
    payload = {
        "query": text,
        "file_id": "",
        "entity_id": ENTITY_ID,
        "k": TOP_K,
    }
    resp = requests.post(
        f"{RAG_API_URL}/query",
        headers={**AUTH_HEADERS, "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    response = resp.json()
    if isinstance(response, dict):
        results = response.get("results", [])
        return results if isinstance(results, list) else []
    return response if isinstance(response, list) else []


def extract_label(content: str) -> str:
    match = re.search(r"LABEL:\s*(TRUSTWORTHY|MALICIOUS)", content, re.IGNORECASE)
    return match.group(1).lower() if match else "unknown"


@mcp.tool()
def classify_article(url: str) -> str:
    """
    Classify a news article URL as trustworthy or malicious.

    The tool fetches the article, compares it against stored reference articles
    using semantic similarity, and returns a simple verdict summary.
    """

    try:
        text = fetch_article_text(url)
    except Exception as exc:  # noqa: BLE001
        return f"Could not fetch article: {exc}"

    try:
        results = query_similar(text)
    except Exception as exc:  # noqa: BLE001
        return f"Could not query vectordb: {exc}"

    if not results:
        return "No similar articles found in the reference database."

    label_counts = {"trustworthy": 0, "malicious": 0}
    matches: list[str] = []
    for result in results:
        content = str(result.get("content", ""))
        score = float(result.get("similarity", result.get("score", 0)) or 0)
        label = extract_label(content)
        if label in label_counts:
            label_counts[label] += 1
        matches.append(f"  - {label.upper()} (similarity: {score:.3f})")

    trustworthy = label_counts["trustworthy"]
    malicious = label_counts["malicious"]
    total = trustworthy + malicious

    if total == 0:
        verdict = "UNCERTAIN"
        explanation = "Could not determine label from reference articles."
    elif malicious > trustworthy:
        verdict = "MALICIOUS"
        explanation = f"Matched {malicious}/{total} malicious reference articles."
    elif trustworthy > malicious:
        verdict = "TRUSTWORTHY"
        explanation = f"Matched {trustworthy}/{total} trustworthy reference articles."
    else:
        verdict = "UNCERTAIN"
        explanation = (
            f"Even split: {trustworthy} trustworthy, {malicious} malicious matches."
        )

    match_list = "\n".join(matches)
    return f"Verdict: {verdict}\n\n{explanation}\n\nTop {len(results)} matches:\n{match_list}"


if __name__ == "__main__":
    mcp.run(transport="sse")
