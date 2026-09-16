# Demo: 5 Worked Examples

Fill in the "Actual result" and screenshot columns after running each
command locally. Structure follows what the assignment asks for: question ->
LLM's understood intent -> tool called -> query executed -> raw result ->
final answer.

---

### 1. New schemes in the last 30 days (citizen)
```bash
python groq_client.py --role citizen "Pichle 30 dino mein kaun-kaun si nayi sarkari schemes launch hui hain?"
```
- **Intent**: list recently released schemes
- **Tool called**: `get_new_schemes(days=30, size=5)`
- **OpenSearch query**: sorted search on `schemes` index by `release_date` desc
- **Result**: [paste tool output here]
- **Final answer**: [paste LLM's final response here]

### 2. Top 5 popular services (citizen)
```bash
python groq_client.py --role citizen "Is mahine sabse zyada search kiye jaane wale top 5 citizen services kaunse hain?"
```
- **Tool called**: `get_popular_services(top_n=5)`
- **OpenSearch query**: `services` index sorted by `search_frequency` desc

### 3. Payment failure error logs (admin)
```bash
python groq_client.py --role admin "Portal par payment failure se jude system error logs nikaalo"
```
- **Tool called**: `fetch_system_error_logs(category="payment_failure")`
- **OpenSearch query**: filtered search on `error_logs` index

### 4. Applications submitted count (admin)
```bash
python groq_client.py --role admin "Kal total kitne citizens ne applications submit kiye?"
```
- **Tool called**: `get_transaction_counts(date="<yesterday's date>")`
- **OpenSearch query**: `_count` API on `applications` index (aggregation, not raw docs)

### 5. Document/guideline search by topic (citizen)
```bash
python groq_client.py --role citizen "Aadhaar update se jude official guidelines do"
```
- **Tool called**: `get_service_guidelines(topic="Aadhaar update")`
- **OpenSearch query**: full-text match on `public_guidelines` index

### 6. Application status count (admin) -- new tool added during POC hardening
```bash
python groq_client.py --role admin "kitne applications approved hain"
```
- **Tool called**: `get_application_status_count(status="approved")`
- **OpenSearch query**: `_count` API on `applications` index filtered by `status`
- **Result**: `{"status": "approved", "count": 2}`
- **Final answer**: "There are 2 approved applications."
- **Note**: added after observing the LLM correctly declined to answer a
  status-based count when no matching tool existed -- see docs/03_security_rbac.md

### 7. Multi-tool chaining (citizen)
```bash
python groq_client.py --role citizen "sabse popular service kaunsi hai aur uski guidelines bhi batao"
```
- **Tools called (in order)**: `get_popular_services(top_n=5)` then
  `get_service_guidelines(topic="Aadhaar Address Update")` -- the second
  call's argument comes from the first call's result, not from the user's
  question directly
- **Final answer**: "The most popular service is Aadhaar Address Update
  (see https://uidai.gov.in/update-address). Guidelines ... are available
  here: https://uidai.gov.in/guidelines."
- **Demonstrates**: the LLM combining two tools to answer a single
  compound question, per Section 3 of the assignment

### 8. Official opensearch-mcp-server-py + Groq (not the custom server)
```bash
python official_groq_client.py "Search the schemes index for anything about farmers"
```
- **Server used**: the official `opensearch-mcp-server-py` package (via
  `uvx`), started with `OPENSEARCH_NO_AUTH=true` -- not `mcp_server.py`
- **Tool called**: `SearchIndexTool` with a Query DSL body targeting the
  `schemes` index
- **Final answer**: returned the PM-Kisan Samman Nidhi scheme with its
  description, eligibility, and release date
- **Demonstrates**: the official OpenSearch MCP server can also be wired
  directly to an LLM, satisfying the assignment's literal architecture
  diagram (`User -> LLM -> OpenSearch MCP -> OpenSearch -> Results -> LLM -> User`)

### 9. Combining independent tools (admin) -- date-range + error-log lookup
```bash
python groq_client.py --role admin --debug "Pichle 2 dino ka total transaction count batao aur agar koi payment_failure wale error logs hain to wo bhi dikhao"
```
- **Tools called (3 separate calls)**: `get_transaction_counts(date="2026-09-14")`,
  `get_transaction_counts(date="2026-09-13")`, and
  `fetch_system_error_logs(category="payment_failure")`
- **Final answer**: combined the two daily counts into a total (4
  transactions across 2 days) and presented the 3 matching payment-failure
  logs as a table
- **Demonstrates**: the LLM decomposing one compound natural-language
  question into several independent tool calls and synthesising all the
  results into a single, readable answer -- something a raw OpenSearch
  query interface would require the user to do manually across two
  separate requests
---

## Before vs After comparison (for the assignment's Task 3)
| | Traditional keyword search | This POC (LLM + MCP) |
|---|---|---|
| Query | Must match exact indexed words | Natural language / Hinglish |
| "Aadhar link do" | Often 0 results (no literal match) | LLM infers intent, calls `get_service_guidelines` |
| "How many X yesterday" | Not possible without a custom report | `get_transaction_counts` aggregation, single number |
| Output | Raw list of links | Natural-language answer with source URLs |
