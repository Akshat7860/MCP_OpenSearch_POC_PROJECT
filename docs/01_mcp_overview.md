# MCP (Model Context Protocol) — Overview

## What is MCP?
MCP is an open, standardized protocol that lets an AI model (LLM) discover
and call external "tools" (functions) exposed by a separate server process,
instead of the model needing custom, hardcoded integration code for every
data source or API.

## Why is it needed?
- LLMs cannot safely be given direct database credentials or raw API access.
- Without a standard, every new data source needs bespoke glue code between
  the LLM and that source.
- MCP standardizes this: any MCP-compatible client (Claude Desktop, a custom
  Groq app, Cursor, etc.) can talk to any MCP server the same way.

## How it works (high level)
1. The MCP **server** registers a set of tools (e.g. `search_government_schemes`)
   with names, descriptions, and input schemas.
2. The MCP **client** (embedded in the LLM application) fetches this tool list.
3. The LLM is given the tool list as part of its context. When it decides a
   tool is needed to answer the user, it emits a structured "tool call".
4. The client forwards that call to the MCP server, which executes the real
   operation (e.g. an OpenSearch query) and returns a JSON result.
5. The result is fed back to the LLM, which uses it to produce the final
   natural-language answer.

```
 User -> LLM (Client) -> MCP Server -> OpenSearch -> MCP Server -> LLM -> User
```

## Key components
| Component | Role |
|---|---|
| **Host / Client** | The LLM application (here: `groq_client.py`) that talks to the MCP server |
| **Server** | Exposes tools over a transport (stdio, SSE, HTTP) — here: `mcp_server.py` |
| **Tools** | Callable functions with a name, description, and JSON schema for arguments |
| **Resources** | (Not used in this POC) static/dynamic content a server can expose for context, separate from tools |
| **Transport** | How client and server talk — stdio (local subprocess) is used in this POC |

## Benefits
- LLM never touches raw credentials or the database directly.
- One standard interface works across many LLM providers and clients.
- Tool descriptions double as documentation the LLM uses to decide *when*
  to call a tool.
- Easy to add role-based tool sets (citizen vs admin) without changing the
  LLM integration code.

## Limitations
- Adds a network/process hop (client -> server -> data source) vs a direct
  API call — negligible for this POC's scale, matters at high throughput.
- Tool descriptions must be precise; vague descriptions cause the LLM to
  call the wrong tool or the right tool with bad arguments.
- MCP itself doesn't enforce authorization — the server code must implement
  that (see `docs/03_security_rbac.md`).
- Multi-step tool chains increase latency (each round trip = one more LLM
  call) and token usage.
