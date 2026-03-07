"""
MCP server: Article Classifier
"""

import os
import re
import requests
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

RAG_API_URL = os.environ.get("RAG_API_URL", "http://rag_api:8000")
JWT_TOKEN   = os.environ.get("JWT_TOKEN", "")
ENTITY_ID   = "article-classifier"
TOP_K       = 5

AUTH_HEADERS = {"Authorization": f"Bearer {JWT_TOKEN}"}
FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"
}

mcp = FastMCP(
    "article-classifier",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )
)

def fetch_article_text(url):
    resp = requests.get(url, headers=FETCH_HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=Tru    return soup.get_text(sep(text):
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
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()

def extract_label(content):
    match = re.search(r"LABEL:\s*(TRUSTWORTHY|MALICIOUS)", content, re.IGNORECASE)
    return match.group(1).lower() if match else "unknown"

@mcp.tool()
def classify_article(url: str) -> str:
    """
    Classify a news article URL as trustworthy or malicious.
    Fetches the article, compares it to stored reference articles using semantic similarity,
    and returns a verdict.

    Args:
        url: The URL of the article to classify
    """
    try:
        text = fetch_article_text(url)
    except Exception as e:
        return f"Could not fetch article: {e}"

    try:
        results = query_similar(text)
    except Exception as e:
        return f"Could not query vectordb: {e}"

    if not results:
        return "No similar articles found in the reference database."

    label_counts = {"trustworthy": 0, "ma    label_counts = {"trustworthy": 0, "ma    label_counts = {"trust        content = r.get("content", "")
        score   = r.get("similarity", r.get("score", 0))
        label   = extract_label(content)
        label_counts[label] += 1
        matches.append(f"  - {label.upper()} (similarity: {score:.3f})")

    t = label_counts["trustworthy"]
    m = label_counts["malicious"]
    total = t + m

    if total == 0:
        verdict = "UNCERTAIN"
        explanation = "Could not determine label from reference articles."
    elif m > t:
        verd        verd        ver   explanation         verd        verd        ver   expla articles."
    elif t > m:
        verdict = "TRUSTWORTHY"
        explanation = f"Matched {t}/{total} trustworthy reference articles."
    else:
        verdict = "UNCERTAIN"
        explanation = f"Even split: {t} trustworthy, {m} malicious matches."

                     .join(matches)
    return f"Verdict: {verdict}\n\n{explanation}\n\nTop {len(results)} matches:\n{match_list}"

if __name__ == "__main__":
    mcp.run(transport="sse")
