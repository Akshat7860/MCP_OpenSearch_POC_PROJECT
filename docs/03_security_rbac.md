# Security & RBAC Approach (POC-level)

## What this POC demonstrates
**Tool-gating at the client level**: `groq_client.py --role citizen` only
ever sends the citizen tool subset to the LLM's tool list. Admin tools
(`fetch_system_error_logs`, `get_transaction_counts`) are filtered out
entirely before the LLM even sees they exist — the model cannot call a
tool it was never told about.

```
Citizen role  -> tools: search_government_schemes, get_new_schemes,
                        get_popular_services, get_service_guidelines,
                        track_application_status

Admin role    -> citizen tools + fetch_system_error_logs,
                                 get_transaction_counts
```

## What this POC does NOT fully implement (documented, not built)
1. **OpenSearch native RBAC (security plugin)** — the Docker cluster runs
   with `DISABLE_SECURITY_PLUGIN=true` for simplicity. In production you
   would enable OpenSearch's security plugin and define:
   - a `citizen` role restricted to `schemes`, `services`, `public_guidelines` indices (read-only)
   - an `admin` role with access to `error_logs`, `applications`
   - map application users to these roles via OpenID Connect / OAuth
2. **True process-level separation** — in production, citizen and admin
   should be two separate MCP server deployments so admin tool *code* is
   never shipped to a public-facing client at all, rather than filtered
   at runtime as this POC does.
3. **Authenticated sessions** — this POC takes `--role` as a CLI flag for
   demo purposes; a real system would derive the role from a verified
   login session, not a client-supplied parameter.

## Why the LLM never gets raw database access
The LLM only ever sees tool names + JSON schemas (via `groq_client.py`'s
`mcp_tool_to_groq_schema`). Credentials for OpenSearch live only inside
`mcp_server.py` / `.env`, never in a prompt or tool-call payload.
