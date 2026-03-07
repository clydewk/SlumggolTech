# Article Classifier — LibreChat MCP Server

A local AI-powered article classifier that detects whether a news article is **trustworthy** or **malicious** using semantic similarity search.

Built on top of [LibreChat](https://github.com/danny-avila/LibreChat) with a custom MCP (Model Context Protocol) server.

---

## How It Works

1. A set of reference articles (labelled `trustworthy` or `malicious`) are embedded and stored in a local vector database
2. When you paste a URL in LibreChat, the MCP tool fetches the article, embeds it, and finds the most semantically similar reference articles
3. The verdict (`TRUSTWORTHY` or `MALICIOUS`) is returned based on which label dominates the top matches

---

## Architecture

```
LibreChat (port 3080)
    └── calls MCP tool
            └── mcp-classifier (port 8001)
                    ├── fetches article from URL
                    ├── queries rag_api (port 8000) for similar articles
                    └── rag_api queries vectordb (pgvector, port 5432)
```

---

## Prerequisites

- [Docker Desktop](https://docs.docker.com/get-docker/)
- [Git](https://git-scm.com/)
- OpenAI API key
- Python 3.9+ (for running the ingestion script)

---

## Setup Guide

### Step 1: Clone LibreChat

```bash
git clone https://github.com/danny-avila/LibreChat.git
cd LibreChat
```

### Step 2: Configure `.env`

```bash
cp .env.example .env
nano .env
```

Set the following values:

```env
OPENAI_API_KEY=sk-your-openai-key-here
EMBEDDINGS_PROVIDER=openai
EMBEDDINGS_MODEL=text-embedding-3-small
```

### Step 3: Add the MCP classifier

Copy the `mcp-classifier/` folder from this repo into your LibreChat directory:

```bash
cp -r mcp-classifier/ ~/Documents/LibreChat/mcp-classifier/
cp ingest_articles.py ~/Documents/LibreChat/ingest_articles.py
```

### Step 4: Create `docker-compose.override.yml`

Create this file in your LibreChat directory:

```yaml
services:
  api:
    volumes:
      - ./librechat.yaml:/app/librechat.yaml

  rag_api:
    ports:
      - "8000:8000"

  mcp-classifier:
    build: ./mcp-classifier
    container_name: mcp-classifier
    environment:
      - RAG_API_URL=http://rag_api:8000
      - JWT_TOKEN=your-jwt-token-here
    ports:
      - "8001:8001"
    restart: always
```

### Step 5: Create `librechat.yaml`

```yaml
version: 1.3.5
cache: true

mcpSettings:
  allowedDomains:
    - "mcp-classifier"

mcpServers:
  article-classifier:
    type: streamable-http
    url: http://mcp-classifier:8001/mcp/
    timeout: 30000
    initTimeout: 10000
```

### Step 6: Start LibreChat

```bash
cd LibreChat
docker compose up -d
```

### Step 7: Generate a JWT token

```bash
docker exec LibreChat node -e "
const jwt = require('jsonwebtoken');
const token = jwt.sign(
  { id: 'ingest-script', role: 'admin' },
  'YOUR_JWT_SECRET_FROM_ENV',
  { expiresIn: '24h' }
);
console.log(token);
"
```

Copy the token and paste it into `docker-compose.override.yml` as `JWT_TOKEN`.

### Step 8: Ingest reference articles

Install dependencies and run the ingestion script:

```bash
pip3 install requests beautifulsoup4
export JWT_TOKEN=your-jwt-token-here
python3 ingest_articles.py
```

All 10 reference articles should be stored successfully.

### Step 9: Build and start the MCP classifier

```bash
cd LibreChat
docker compose up -d --build mcp-classifier
docker compose restart api
```

Verify it connected:

```bash
docker logs LibreChat --tail 20 | grep -i "mcp\|tool"
```

You should see:
```
[MCP][article-classifier] Tools: classify_article
[MCP] Initialized with 1 configured server and 1 tool.
```

---

## Using the Classifier

1. Open **http://localhost:3080**
2. Sign up for a local account
3. Go to **Agents** in the left sidebar
4. Create an agent with:
   - **Model**: GPT-4o (or any OpenAI model)
   - **Instructions**: `When the user gives you a URL, always use the classify_article tool to classify it. Return the verdict clearly as TRUSTWORTHY or MALICIOUS.`
   - **Tools**: enable `classify_article`
5. Start a chat and paste any news article URL

---

## Adding More Reference Articles

Edit `ingest_articles.py` and add entries to the `ARTICLES` list:

```python
{"url": "https://example.com/article", "label": "trustworthy"},
{"url": "https://example.com/fake-news", "label": "malicious"},
```

Then re-run:

```bash
python3 ingest_articles.py
```

No restart needed — new articles are immediately available for comparison.

---

## Troubleshooting

**MCP tool not showing in agent builder**
- Check `docker logs LibreChat | grep mcp`
- Ensure `librechat.yaml` is mounted correctly

**JWT token expired**
- Tokens expire after 24h — regenerate with Step 7 above
- Update `JWT_TOKEN` in `docker-compose.override.yml` and restart: `docker compose up -d mcp-classifier`

**Ingestion script fails with connection error**
- Ensure `rag_api` port is exposed: check `docker ps` for port 8000
- Ensure `docker-compose.override.yml` exposes `rag_api` on port 8000

**Classifier always returns UNCERTAIN**
- Check that ingestion completed successfully (10/10 articles)
- Verify `ENTITY_ID` in `server.py` matches the one used during ingestion (`article-classifier`)
