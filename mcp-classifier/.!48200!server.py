"""
MCP server: Article Classifier
"""

import os
import re
import requests
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# ─── Config ───────────────────────────────────────────────────────────────────
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

