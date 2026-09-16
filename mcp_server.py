"""
Custom MCP server for the india.gov.in POC.

Wraps OpenSearch operations as domain-specific tools instead of exposing raw
Query DSL to the LLM. Tools are split into CITIZEN (public) and ADMIN
(internal) groups -- see citizen_client.py / admin_client.py for how each
audience only ever sees its own tool subset (tool-gating).

Run directly for local testing:
    python mcp_server.py
"""
import os
import sys
from opensearchpy import OpenSearch
from datetime import datetime, timedelta
from fastmcp import FastMCP
from dotenv import load_dotenv

# .env file se environment variables (jaise OPENSEARCH_HOST) load karta hai
load_dotenv()

# FastMCP server instance banaya -- "india-gov-poc" iska naam hai
mcp = FastMCP("india-gov-poc")

# OpenSearch se connection banane ke liye client object
# host/port .env se aa rahe hain, agar nahi mile to default localhost:9201 use hoga
client = OpenSearch(
    hosts=[{
        "host": os.getenv("OPENSEARCH_HOST", "localhost"),
        "port": int(os.getenv("OPENSEARCH_PORT", "9201")),
    }],
    use_ssl=os.getenv("OPENSEARCH_USE_SSL", "false").lower() == "true",
    verify_certs=False,
)

# --- FIX: FAIL-FAST VALIDATION ---
# Agar OpenSearch reachable hi nahi hai, turant clear error dekar exit karo,
# taaki baad mein confusing "connection refused" errors tool-calls ke beech mein na aayein
if not client.ping():
    sys.exit(
        "ERROR: Cannot reach OpenSearch. Is `docker compose up -d` running? "
        "Check OPENSEARCH_HOST/OPENSEARCH_PORT in your .env file."
    )

# Kisi bhi tool se ek baar mein max itne hi results aa sakte hain
# Isse LLM ko accidentally bohot bada data payload nahi milega (context overload se bachne ke liye)
MAX_SIZE = 10  # hard cap so the LLM can never accidentally pull huge payloads


def _bump_search_frequency(query_text: str):
    """Best-effort side effect: jab bhi koi citizen kisi topic ki guidelines
    search kare, us topic se closest-matching service ka search_frequency +1
    kar dete hain -- taaki 'popular services' data static na rahe.
    Ye purely cosmetic hai, isliye kabhi bhi actual tool response ko fail
    nahi karna chahiye -- isliye poori tarah try/except mein wrapped hai."""
    try:
        res = client.search(index="services", body={
            "size": 1,
            "query": {"match": {"title": query_text}},
        })
        hits = res["hits"]["hits"]
        if hits:
            doc_id = hits[0]["_id"]
            client.update(index="services", id=doc_id, body={
                "script": {"source": "ctx._source.search_frequency += 1", "lang": "painless"}
            })
    except Exception:
        pass  # non-critical -- kabhi bhi is failure ki wajah se main tool na toote


# ===========================================================================
# CITIZEN TOOLS (public data only: schemes, services, public_guidelines)
# ===========================================================================

# @mcp.tool() decorator lagate hi ye normal Python function
# automatically ek MCP tool ban jaata hai jise LLM call kar sakta hai
@mcp.tool()
def search_government_schemes(query: str, size: int = 5) -> list:
    """Search government welfare schemes by natural language keywords
    (e.g. farmer support, health insurance, girl child savings)."""
    # size ko MAX_SIZE se zyada nahi hone dete (safety limit)
    size = min(size, MAX_SIZE)
    body = {
        "size": size,
        # _source: sirf ye fields chahiye, baaki OpenSearch ka extra metadata mat bhejo
        "_source": ["title", "url_link", "description", "eligibility", "release_date"],
        # multi_match: user ke query ko title/description/eligibility teeno fields mein search karo
        "query": {"multi_match": {"query": query, "fields": ["title", "description", "eligibility"]}},
    }
    # OpenSearch ke "schemes" index par ye query chalao
    res = client.search(index="schemes", body=body)
    # Sirf actual data (_source) return karo, OpenSearch ka wrapper JSON nahi
    return [h["_source"] for h in res["hits"]["hits"]]


@mcp.tool()
def get_new_schemes(days: int = 30, size: int = 5) -> list:
    """List government schemes released within the last N days."""
    size = min(size, MAX_SIZE)
    # "aaj se N din pehle" ki date calculate karo (date-range filter ke liye)
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    body = {
        "size": size,
        "_source": ["title", "url_link", "release_date"],
        # Sabse naye scheme pehle dikhane ke liye descending sort
        "sort": [{"release_date": {"order": "desc"}}],
        # range query: release_date cutoff_date se aaj tak ka hona chahiye (gte = greater than or equal)
        "query": {"range": {"release_date": {"gte": cutoff_date}}},
    }
    res = client.search(index="schemes", body=body)
    return [h["_source"] for h in res["hits"]["hits"]]


@mcp.tool()
def get_popular_services(top_n: int = 5) -> list:
    """Get the top N most-searched citizen services, ranked by popularity."""
    top_n = min(top_n, MAX_SIZE)
    body = {
        "size": top_n,
        "_source": ["title", "url_link", "search_frequency"],
        # search_frequency ke hisaab se sabse popular service sabse upar
        "sort": [{"search_frequency": {"order": "desc"}}],
    }
    res = client.search(index="services", body=body)
    return [h["_source"] for h in res["hits"]["hits"]]


@mcp.tool()
def get_service_guidelines(topic: str, size: int = 3) -> list:
    """Get official guidelines, required documents, or circulars for a topic
    (e.g. Aadhaar update, PAN linkage, income certificate)."""
    size = min(size, MAX_SIZE)
    body = {
        "size": size,
        "_source": ["title", "url_link", "description"],
        # match query: description field mein topic ke keywords dhoondo (full-text search)
        "query": {"match": {"description": topic}},
    }
    res = client.search(index="public_guidelines", body=body)
    # FIX: har guidelines-lookup ko ek "popularity signal" bhi treat karte hain
    _bump_search_frequency(topic)
    return [h["_source"] for h in res["hits"]["hits"]]


@mcp.tool()
def track_application_status(application_id: str) -> dict:
    """Look up the current status of a citizen's application by its ID."""
    # FIX: ".keyword" hataya -- ab application_id explicitly "keyword" type
    # hai mapping mein, isliye field pe direct term-match kaam karega
    body = {"size": 1, "query": {"term": {"application_id": application_id}}}
    try:
        res = client.search(index="applications", body=body)
    except Exception:
        # safety fallback, agar mapping kabhi dynamic ban jaaye future mein
        res = client.search(index="applications", body={"size": 1, "query": {"match": {"application_id": application_id}}})
    hits = res["hits"]["hits"]
    # Agar application mila to uska data do, warna error message
    return hits[0]["_source"] if hits else {"error": "Application not found"}


# ===========================================================================
# ADMIN TOOLS (internal data: error_logs, applications/transactions)
# These must NEVER be registered in a citizen-facing client. See README.
# ===========================================================================

# Ye tools sirf admin role ke liye hain -- citizen client mein ye visible nahi hone chahiye
# (tool-gating security groq_client.py mein implement hoti hai)
@mcp.tool()
def fetch_system_error_logs(category: str = "", size: int = 5) -> list:
    """[ADMIN ONLY] Fetch recent system error logs, optionally filtered by
    error category (e.g. payment_failure, timeout, validation_error)."""
    size = min(size, MAX_SIZE)
    # Agar category di hai to usi se filter karo, warna sab logs do
    query = {"match_all": {}} if not category else {"match": {"error_type": category}}
    body = {
        "size": size,
        "_source": ["timestamp", "service", "error_type", "status"],
        # Sabse latest error pehle dikhane ke liye
        "sort": [{"timestamp": {"order": "desc"}}],
        "query": query,
    }
    res = client.search(index="error_logs", body=body)
    return [h["_source"] for h in res["hits"]["hits"]]


@mcp.tool()
def get_transaction_counts(date: str = "") -> dict:
    """[ADMIN ONLY] Get the count of applications/transactions submitted,
    optionally filtered to a specific date (YYYY-MM-DD). Returns a single
    aggregated number, never raw documents."""
    # Agar date di hai to usi din ka data count karo, warna sabka
    query = {"match_all": {}} if not date else {"term": {"date": date}}
    # client.count() -- OpenSearch ke andar hi count calculate hota hai,
    # LLM ko poore documents nahi bhejne padte, sirf ek number milta hai
    res = client.count(index="applications", body={"query": query})
    return {"date": date or "all-time", "count": res["count"]}


@mcp.tool()
def get_application_status_count(status: str = "approved") -> dict:
    """[ADMIN ONLY] Get the count of applications filtered by a specific
    status (e.g. approved, pending, submitted, under_review, rejected)."""
    body = {"query": {"match": {"status": status.lower()}}}
    res = client.count(index="applications", body=body)
    return {"status": status, "count": res["count"]}


# Ye file directly run hone par (python mcp_server.py) MCP server start ho jayega
if __name__ == "__main__":
    mcp.run()
