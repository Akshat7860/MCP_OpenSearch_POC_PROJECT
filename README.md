# 🏛️ india.gov.in Conversational Search POC
**Model Context Protocol (MCP) + OpenSearch + Groq LLM**

An enterprise-grade Proof of Concept (POC) demonstrating Agentic RAG: `User ➡️ LLM ➡️ MCP ➡️ OpenSearch ➡️ Results ➡️ LLM ➡️ User`. 

This system replaces traditional keyword-based searches with a natural-language conversational interface (Hindi/Hinglish/English) to query government-style data, featuring strict Role-Based Access Control (RBAC) and a fail-closed Python security gatekeeper.

📖 **Full Technical Documentation:** See the `docs/` folder for deep dives into [MCP Architecture](docs/01_mcp_overview.md), [OpenSearch Tooling](docs/02_opensearch_mcp.md), and [RBAC Security Implementation](docs/03_security_rbac.md).

---

## 🚀 Prerequisites

Before you begin, ensure you have the following installed:
*   **Docker & Docker Compose**
*   **Python 3.10+**
*   **Groq API Key:** Get a free key from [Groq Console](https://console.groq.com/keys)

---

## 🛠️ Quick Setup Guide

### 1. Start the OpenSearch Cluster
Spin up the local OpenSearch database and dashboards:
```bash
docker compose up -d
