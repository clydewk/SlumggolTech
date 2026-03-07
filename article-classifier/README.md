# Article Classifier — LibreChat MCP Server

A local AI-powered article classifier that detects whether a news article is **trustworthy** or **malicious** using semantic similarity search against a curated reference set.

> **Note:** This is a proof of concept. See the [Limitations](#limitations) section for an honest breakdown of what this system can and cannot do.

---

## How It Works

1. A set of reference articles (labelled `trustworthy` or `malicious`) are embedded and stored in a local vector database (pgvector)
2. When you paste a URL in LibreChat, the MCP tool fetches the article text, queries the vector database for the most semantically similar reference articles, and returns a verdict based on the majority label
3. The verdict (`TRUSTWORTHY`, `MALICIOUS`, or `UNCERTAIN`) is returned with a breakdown of the top matches

---

## Architecture

```
LibreChat (port 3080)
    └── Agent calls MCP tool
            └── mcp-classifier (port 8001)
                    ├── fetches article text from URL (1MB max, SSL optional)
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

### Step 3: Add the MCP classifier files

Copy the contents of this repo into your LibreChat directory:

```bash
cp -r article-classifier/mcp-classifier ~/Documents/LibreChat/mcp-classifier
cp article-classifier/ingest_articles.py ~/Documents/LibreChat/ingest_articles.py
cp article-classifier/librechat.yaml ~/Documents/LibreChat/librechat.yaml
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

### Step 5: Start LibreChat

```bash
cd LibreChat
docker compose up -d
```

### Step 6: Generate a JWT token

The rag_api requires a JWT token for authentication. Generate one using your JWT_SECRET from `.env`:

```bash
docker exec LibreChat node -e "
const jwt = require('jsonwebtoken');
const token = jwt.sign(
  { id: 'ingest-script', role: 'admin' },
  'YOUR_JWT_SECRET_FROM_ENV',
  { expiresIn: '365d' }
);
console.log(token);
"
```

Copy the token and update `JWT_TOKEN` in `docker-compose.override.yml`. Then restart the MCP container:

```bash
docker compose up -d mcp-classifier
```

### Step 7: Ingest reference articles

Install dependencies and run the ingestion script:

```bash
pip3 install requests beautifulsoup4
export JWT_TOKEN=your-jwt-token-here
python3 ingest_articles.py
```

All 10 reference articles should be stored successfully (10/10).

### Step 8: Build and start the MCP classifier

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

## Creating the Agent in LibreChat

1. Open **http://localhost:3080** and sign up for a local account
2. Click **Agents** in the left sidebar
3. Click **Create Agent** and configure:
   - **Name**: `Article Classifier`
   - **Model**: GPT-4o (or any OpenAI model)
   - **Instructions**: `When the user gives you a URL, always use the classify_article tool to classify it. Return the verdict clearly as TRUSTWORTHY or MALICIOUS.`
   - **Tools**: enable `classify_article`
4. Save the agent

### Get the agent ID for librechat.yaml

```bash
docker exec chat-mongodb mongosh LibreChat --eval "db.agents.findOne({name: 'Article Classifier'}, {id: 1, name: 1})"
```

Update `librechat.yaml` with the `id` field (not `_id`):

```yaml
modelSpecs:
  enforce: true
  prioritize: true
  list:
    - name: "Article Classifier"
      label: "Article Classifier"
      description: "Paste a news article URL to classify it as trustworthy or malicious"
      preset:
        default: true
        endpoint: "agents"
        agent_id: "agent_xxxxxxxxxxxxx"
```

Then restart:

```bash
docker compose restart api
```

---

## Usage

Open **http://localhost:3080** and paste any news article URL in the chat. The agent will classify it as `TRUSTWORTHY` or `MALICIOUS` with a breakdown of the top matching reference articles.

---

## Adding More Reference Articles

Edit `ingest_articles.py` and add entries to the `ARTICLES` list:

```python
{"url": "https://example.com/article", "label": "trustworthy"},
{"url": "https://example.com/fake-news", "label": "malicious"},
```

Then re-run:

```bash
export JWT_TOKEN=your-jwt-token-here
python3 ingest_articles.py
```

No restart needed.

---

## Limitations

Understanding these limitations is important before using this classifier in any real-world context.

### 1. It classifies by similarity, not by truth

This system does not actually fact-check articles or verify whether the information in them is accurate. Instead, it compares a new article to your 10 reference articles and asks: *does this article feel more like the trustworthy ones or the malicious ones?*

Think of it like a student who has only read 10 books — 5 good ones and 5 bad ones. If you hand them a new book, they can only tell you whether it reminds them more of the good pile or the bad pile. They cannot tell you if the new book is actually well-written or factually correct on its own merits.

### 2. Topic overlap causes misclassification

The system compares articles based on their **topic and writing style** together. This means an article about the Middle East war (a topic covered in the trustworthy reference set) might incorrectly match a malicious article about Middle Eastern finance just because both mention the same region. The system has no way to separate "this article is about a similar topic" from "this article is from a similar quality source."

For example, a well-written CNA article about the Iran-Israel war may get flagged as MALICIOUS if the top matching reference articles happen to be malicious articles that also mention the Middle East or financial markets.

### 3. Only 10 reference articles is very few

With only 5 trustworthy and 5 malicious examples, the classifier has a very narrow view of what each category looks like. A new article on any topic not well-represented in those 10 examples will produce unreliable results. In machine learning terms, this is called **underfitting** — the model has not seen enough examples to generalise well.

To put this in perspective: professional content moderation systems are trained on millions of examples. This system has 10.

### 4. It cannot detect sophisticated misinformation

Malicious or misleading articles are not always poorly written. Some of the most dangerous misinformation looks completely professional — correct grammar, credible-sounding domain names, real-looking author names, and plausible-sounding facts mixed with false claims. This system has no way to detect that kind of content because it only looks at surface-level text similarity, not factual accuracy.

### 5. SSL errors on suspicious sites are handled silently

Many low-quality or malicious websites have invalid or expired SSL certificates. The classifier is configured to ignore SSL errors (`verify=False`) so it can still fetch and analyse these sites. This is intentional — a bad SSL certificate is itself a signal of a sketchy site — but it means the system will fetch content from sites that your browser would normally warn you about.

### 6. The verdict is not a definitive judgement

The output (`TRUSTWORTHY` or `MALICIOUS`) is a statistical guess based on similarity scores, not a definitive label. An `UNCERTAIN` result means the top matches were evenly split. Even a confident `TRUSTWORTHY` verdict does not mean the article is factually correct — it only means the article text resembles the trustworthy reference articles more than the malicious ones.

Always use this tool as one signal among many, not as a final answer.

---

## Potential Improvements

For anyone looking to extend this proof of concept into something more robust:

- **Add more reference articles** — even 50-100 per category would significantly improve accuracy
- **Use LLM-based analysis** — instead of pure vector similarity, pass the article to an LLM with a prompt that checks for red flags like missing authors, sensationalist language, unknown domains, and lack of citations
- **Add domain reputation checking** — cross-reference the article's domain against known lists of reliable and unreliable news sources
- **Separate topic from quality** — use embeddings that capture writing quality and source signals independently from topic content

---

## Troubleshooting

**JWT token expired**
Tokens are set to 365d expiry. If expired, regenerate with Step 6 and update `docker-compose.override.yml`, then restart: `docker compose up -d mcp-classifier`

**MCP tool not showing in agent builder**
Check `docker logs LibreChat | grep mcp` and ensure `librechat.yaml` is mounted correctly.

**Classifier returns wrong verdict**
The vector similarity approach works best when reference articles are topically diverse. Add more reference articles covering a wider range of topics to improve accuracy.

**SSL errors on malicious URLs**
Expected — many malicious sites have invalid SSL certificates. The classifier handles this with `verify=False` and fetches the content anyway.

---

## Security Considerations

This system is designed to run entirely on your local machine. No article content, embeddings, or verdicts are sent to any external server other than OpenAI's embedding API. That said, there are a few security considerations worth understanding.

### SSL Verification is Disabled for Article Fetching

When fetching articles from URLs, the classifier disables SSL certificate verification (`verify=False`). This is intentional — many low-quality and malicious websites have expired or self-signed certificates, and refusing to fetch them would make the classifier useless for exactly the sites it needs to analyse.

The risk is low because the classifier only **reads** content from these sites. It never submits any data to them. However, you should be aware that disabling SSL verification means the system cannot detect man-in-the-middle attacks if someone were to intercept the request between your machine and the target site. In a local, offline-first setup this is not a practical concern.

### Content Size is Capped at 1MB

To protect against malicious sites serving oversized responses designed to exhaust memory or crash the server, all HTTP responses are hard-capped at **1MB** before any parsing occurs. After parsing, only the first 3,000 characters of extracted text are used for embedding. A typical news article is between 5KB and 100KB, so the 1MB cap is generous enough to never affect legitimate content while still providing a meaningful safety boundary.

### JWT Tokens Protect the Vector Database

The local `rag_api` service (which stores and queries embeddings) requires a signed JWT token for every request. This token is generated using a secret key stored in your `.env` file and is never exposed outside your machine. Tokens are set to a 365-day expiry for convenience in local development. If you ever suspect your token has been compromised, regenerate it using the command in Step 6 of the setup guide and update `docker-compose.override.yml`.

### The Vector Database is Not Exposed to the Internet

The `vectordb` (pgvector/PostgreSQL) container only listens on Docker's internal network. It is not bound to any public port on your machine, so it cannot be accessed from outside your local environment. The `rag_api` container is the only service that communicates with it directly.

### Article Content is Not Persisted

When a new URL is classified, the fetched article text is held in memory only for the duration of the request. It is embedded, compared against reference articles, and then discarded. No new article content is written to the vector database unless you explicitly run `ingest_articles.py`.

---

## Rate Limiting and Database Protection

The classifier is designed to avoid overwhelming the local vector database, which runs on modest hardware inside a Docker container. Several safeguards are in place.

### Ingestion is Throttled with a Delay

When ingesting reference articles via `ingest_articles.py`, a **0.5 second delay** is inserted between each article. This prevents the embedding API and the vector database from receiving a burst of simultaneous write requests. For 10 articles this is barely noticeable, but it becomes important when ingesting hundreds of articles at once.

### Each Classification is a Single Sequential Request

A classification request follows a strict sequential pipeline: fetch article → query vector database → return result. There is no parallelism or batching of queries during classification. This means the vector database only ever handles one query at a time per user request, which keeps the load predictable and low.

### Query Result Count is Bounded

The system retrieves a maximum of **10 candidate results** from the vector database per query (`TOP_K = 10`), then narrows them down to the 5 closest matches (`VERDICT_K = 5`) in memory. This means the database never has to return or scan more rows than necessary, keeping query time fast and memory usage low even as the reference set grows.

### HTTP Timeouts are Enforced on All Requests

Every outbound HTTP request — whether fetching an article from a URL or querying the `rag_api` — has an explicit timeout. Article fetches time out after **15 seconds**, and vector database queries time out after **30 seconds**. This ensures that a slow or unresponsive external site can never cause the classifier to hang indefinitely and block other requests.

### The 1MB Content Cap Protects Parsing Memory

As mentioned in the Security section, article responses are capped at 1MB before being passed to the HTML parser. Without this cap, a site serving a 50MB HTML response could spike memory usage during parsing and potentially destabilise the container. The cap ensures the BeautifulSoup parser never has to handle more than 1MB of raw HTML regardless of what the remote server sends.
