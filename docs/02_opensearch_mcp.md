# OpenSearch MCP — What's Official vs What We Built

## Official `opensearch-mcp-server-py`
Installable via `pip install opensearch-mcp-server-py` or run directly with
`uvx opensearch-mcp-server-py`. Out of the box it exposes **generic**
OpenSearch operations, not domain-named tools:

| Tool | Purpose |
|---|---|
| `ListIndexTool` | List all indices with doc counts, sizes |
| `IndexMappingTool` | Get an index's field mapping/schema |
| `SearchIndexTool` | Run a raw Query DSL search against an index |
| `CountTool` | Count matching documents |
| `GetShardsTool` | Shard-level cluster info |
| `ClusterHealthTool` | Cluster health status |
| `GenericOpenSearchApiTool` | Escape hatch for arbitrary OpenSearch REST calls |
| `MsearchTool` | Multi-search (several queries in one request) |
| `ExplainTool` | Explain why a document did/didn't match a query |

Auth support: basic auth, AWS IAM, AWS profile, header auth, mTLS, or
no-auth (used in this POC since the Docker cluster runs with
`DISABLE_SECURITY_PLUGIN=true`).

## Why this POC uses a custom server instead
The assignment (and the india.gov.in use case) calls for domain-specific,
named tools like `search_government_schemes()` or `fetch_system_error_logs()`
— these don't exist in the official package. Rather than force the LLM to
write raw Query DSL against `SearchIndexTool` (which defeats the purpose of
"no query syntax knowledge required"), this POC wraps OpenSearch directly
using `fastmcp` in `mcp_server.py`, with each tool:
- hardcoding a `size` limit (never dumps unbounded results),
- filtering `_source` fields (drops OpenSearch internals like `_score`, `_shards`),
- using OpenSearch-side aggregations/counts for "how many" questions instead
  of returning raw documents for the LLM to count.

This keeps the same spirit as the official server (LLM never sees raw REST
calls or credentials) while giving cleaner, task-specific tools for the demo.

## Setup prerequisites
- Docker + Docker Compose (for OpenSearch + Dashboards)
- Python 3.10+
- A Groq API key (free tier): https://console.groq.com/keys
