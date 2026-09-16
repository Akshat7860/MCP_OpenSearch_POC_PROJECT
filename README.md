# india.gov.in Conversational Search POC — MCP + OpenSearch + Groq

A proof of concept demonstrating: `User -> LLM -> MCP -> OpenSearch -> Results -> LLM -> User`,
replacing traditional keyword search with natural-language (Hindi/Hinglish/English)
querying over government-style data (schemes, services, applications, error logs).

See `docs/` for the full write-up:
- `docs/01_mcp_overview.md` — what MCP is, why it's needed, how it works
- `docs/02_opensearch_mcp.md` — official OpenSearch MCP tools vs this POC's custom tools
- `docs/03_security_rbac.md` — citizen vs admin tool-gating approach

## Prerequisites
- Docker + Docker Compose
- Python 3.10+
- A free Groq API key: https://console.groq.com/keys

## Setup

### 1. Start OpenSearch
```bash
docker compose up -d
```
Wait ~15-20 seconds, then verify:
```bash
curl -s http://localhost:9201
```
You should see a JSON response with `cluster_name` and `version`. Dashboards
are available at http://localhost:5602.

> If port `9201` or `5602` is already taken on your machine, edit the port
> mappings in `docker-compose.yml` (left side of `"HOST:CONTAINER"`) and
> update `.env` to match.

### 2. Install Python dependencies
```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure environment
```bash
cp .env.example .env
```
Edit `.env` and add your `GROQ_API_KEY`.

### 4. Seed sample data
```bash
python seed_data.py
```
This creates and populates 5 indices: `schemes`, `services`,
`public_guidelines` (public/citizen data) and `error_logs`, `applications`
(internal/admin data).

### 5. (Optional) Sanity-check the MCP server standalone
```bash
npx @modelcontextprotocol/inspector python mcp_server.py
```
Opens a local web UI where you can see the registered tools and manually
call one against your seeded data before wiring up the LLM.

### 6. Run natural-language queries through Groq + MCP

Citizen query:
```bash
python groq_client.py --role citizen "Pichle 30 dino mein kaun-kaun si nayi schemes launch hui?"
```

Admin query:
```bash
python groq_client.py --role admin "Payment failure se jude error logs nikaalo"
```

More examples in `demo/example_queries.md`.

## Project structure
```
.
├── docker-compose.yml       # OpenSearch + Dashboards
├── .env.example
├── requirements.txt
├── seed_data.py              # dummy data loader
├── mcp_server.py              # custom MCP server (citizen + admin tools)
├── groq_client.py             # LLM tool-calling client with role gating
├── docs/                      # technical documentation
└── demo/
    └── example_queries.md     # 5 worked examples for the demo
```

## Known limitations
- Runs with OpenSearch security plugin disabled (POC simplification) —
  see `docs/03_security_rbac.md` for the production hardening path.
- Role selection is a CLI flag, not derived from a real auth session.
- Groq free tier has rate limits; `.env` includes a `GROQ_FALLBACK_MODEL`
  as backup if you hit them.
