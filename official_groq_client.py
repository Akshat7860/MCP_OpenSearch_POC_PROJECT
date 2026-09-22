"""
=====================================================================
OFFICIAL OPENSEARCH MCP CLIENT WITH APPLICATION-LEVEL GATEKEEPER RBAC
=====================================================================
WHAT: Connects Groq LLM to the official opensearch-mcp-server-py package.
WHY: Implements a secure E-Governance RAG pipeline with role-based filtering.

NOTE: OpenSearch native security plugin is ENABLED (DISABLE_SECURITY_PLUGIN=false)
Role-based access (citizen vs admin) is enforced entirely at the
APPLICATION layer below via the gatekeeper (is_call_allowed / allowed_indices),
not via separate OpenSearch service accounts.

CHANGELOG (fixes applied in this version):
  1. FIX: ClusterHealthTool was being blocked for EVERY role, including admin,
     because it takes no index argument and was missing from the no-index
     safe-tool whitelist. Now split into a role-aware whitelist.
  2. FIX: Added per-index descriptions to the system prompt so the LLM stops
     guessing the wrong index (e.g. trying "applications" to look up a
     person's name, when that index only stores application_id/status).
  3. FIX: Added a note clarifying that "list all available tools" style
     meta-questions ARE answerable from the tool schema itself, so the LLM
     doesn't reflexively fall back to "not available" for them.
  4. FIX: Groq's strict JSON-schema validation was rejecting tool calls where
     an OPTIONAL parameter (e.g. ClusterHealthTool's "index") was sent as
     null, because the tool's schema only declared type "string" (not
     nullable). Now every optional parameter's schema is patched to also
     allow "null" before being sent to Groq, and the prompt tells the LLM
     to omit unneeded optional parameters entirely instead of sending null.
  5. FIX: ListIndexTool's raw result is prefixed with a plain-text label
     (e.g. "Indices in the cluster:\\n[...]"), not pure JSON. The old
     Defense-in-Depth filtering code called json.loads() directly on this,
     which silently failed and returned an empty "[]" list every time --
     making it look like no indices existed at all, even for admin. Added
     a helper that extracts just the JSON portion before parsing.
  6. FIX: MsearchTool does not put its target index name(s) in a top-level
     "index"/"indices" argument the way most other tools do. Per the tool's
     own official schema, index names live inside its "body" parameter --
     either as a JSON array of alternating header/query objects, or as
     NDJSON text -- where each "header" object carries an "index" key. The
     old extract_requested_indices() never looked inside "body", so every
     MsearchTool call had requested=[] and was default-denied (the same
     class of bug as fix #1). Added body-aware extraction that parses both
     the array and NDJSON forms and fails CLOSED (blocks the call) if the
     body can't be safely parsed, rather than silently allowing it.
  7. CORRECTION (reverted a wrong fix): an earlier version of this file
     mistakenly added GetShardsTool to ADMIN_ONLY_NO_INDEX_TOOLS, treating
     it like ClusterHealthTool. Per the official opensearch-mcp-server-py
     parameter docs, GetShardsTool's "index" argument is REQUIRED -- there
     is no cluster-wide "list all shards" mode. So a call with no index was
     correctly being blocked; it was never a Gatekeeper bug. Reverted that
     change. The correct pattern for "list all shards" style questions is
     for the LLM to call ListIndexTool first, then call GetShardsTool once
     per index -- which it already does successfully in practice.
  8. FIX: Strengthened the GIVE-UP RULE and INDEX_CATALOG_NOTE so the LLM
     consults the index catalog to pick the single most relevant index
     BEFORE spending its one search attempt, instead of guessing (e.g. a
     service link/URL question maps to "services", not "applications" or
     "error_logs"). Also corrected the catalog's note about which tools are
     index-free (ClusterHealthTool and ListIndexTool only -- NOT
     GetShardsTool, per fix #7).
"""
import argparse
import asyncio
import json
import os
import sys
import logging
import warnings

from dotenv import load_dotenv
# FastMCP client library MCP server se connect hone ke liye use hoti hai
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
# Groq ka official Python SDK LLM completions ke liye
from groq import Groq

# .env file se saari confidential settings (API keys, URLs, credentials) load karte hain
load_dotenv()

# =====================================================================
# 0. FAIL-FAST STARTUP VALIDATION
# =====================================================================
if not os.getenv("GROQ_API_KEY"):
    sys.exit("ERROR: GROQ_API_KEY not set. Check your .env file.")

if not os.getenv("OPENSEARCH_ADMIN_PASS"):
    sys.exit("ERROR: OPENSEARCH_ADMIN_PASS not set. Check your .env file.")

# =====================================================================
# 1. ROLE-BASED ACCESS CONTROL (RBAC) CONFIGURATION
# =====================================================================
CITIZEN_ALLOWED_INDICES = {"schemes", "services", "public_guidelines", "touristplace"}
ADMIN_ALLOWED_INDICES = CITIZEN_ALLOWED_INDICES | {"error_logs", "applications"}

# ---------------------------------------------------------------------
# FIX #1: No-index tools split by role.
# FIX #7 (correction): GetShardsTool REMOVED from this whitelist -- its
# "index" parameter is REQUIRED per the official tool schema, so a call
# with no index is correctly rejected, not a bug to work around.
# ---------------------------------------------------------------------
CITIZEN_SAFE_NO_INDEX_TOOLS = {"ListIndexTool"}
ADMIN_ONLY_NO_INDEX_TOOLS = {"ClusterHealthTool"}


def get_safe_no_index_tools(role: str) -> set:
    """Role ke hisaab se woh saare no-index tools return karta hai jo allowed hain."""
    if role == "admin":
        return CITIZEN_SAFE_NO_INDEX_TOOLS | ADMIN_ONLY_NO_INDEX_TOOLS
    return CITIZEN_SAFE_NO_INDEX_TOOLS


# =====================================================================
# 2. PROMPT ENGINEERING & SYSTEM INSTRUCTIONS
# =====================================================================
# FIX #2 + FIX #8: Har index ka short description, galat index guess se bachne
# ke liye, aur GetShardsTool ke bare mein sahi guidance (index required hai).
INDEX_CATALOG_NOTE = """
AVAILABLE INDICES AND WHAT THEY CONTAIN (use this to pick the right index
BEFORE calling IndexMappingTool, instead of guessing from the question's topic alone):
- schemes: government welfare schemes (title, description, eligibility, release_date).
  No personal/citizen data.
- services: citizen service names, how often people search for them, and their
  official URL/link (title, search_frequency, url_link). This is the correct
  index for ANY question asking for a service's link/URL (e.g. Aadhaar,
  PAN, passport, voter ID, driving license, income certificate). No personal
  data.
- public_guidelines: how-to documentation for using a service (title, description).
  No personal/citizen data.
- touristplace: tourist locations, geography, and travel info (title, description,
  cityName, districtName, stateName, how_to_reach). No personal/citizen data.
- error_logs (admin only): backend system failure logs (timestamp, service,
  error_type, status). Contains NO citizen or applicant names.
- applications (admin only): citizen application STATUS records ONLY
  (application_id, service, date, status). Does NOT contain applicant names,
  phone numbers, or any other personal identifiers.

If a question asks about a person's name, or anything not described above,
do NOT guess an index -- attempt at most one exploratory search (per the
GIVE-UP RULE below) and then use the standard "not available" response.

NOTE on index-free tools: ClusterHealthTool and ListIndexTool do NOT need an
index at all -- call them directly for "what is the cluster health" or "list
all indices" questions, without checking this catalog first. GetShardsTool is
DIFFERENT: it always REQUIRES a specific index. For "list all shards" style
questions, first call ListIndexTool to get the index names, then call
GetShardsTool once per index -- do not attempt GetShardsTool without an index.
"""

BASE_SYSTEM_PROMPT = """You are a helpful assistant with access to a live OpenSearch
cluster through generic OpenSearch MCP tools.

Unlike a domain-specific tool, SearchIndexTool expects a raw OpenSearch Query
DSL body. Before searching an unfamiliar index, first call IndexMappingTool to
learn its fields.

CRITICAL JSON SYNTAX RULE:
When using SearchIndexTool, your `query_dsl` parameter MUST always be properly wrapped in a root `"query"` object. 
Correct Example: {"query": {"match": {"field": "value"}}}

CRITICAL TOOL CALLING RULE:
Always use the EXACT tool name provided in the function list. Never append any extra characters, tags, or tokens to the tool name.

OPTIONAL PARAMETER RULE (MANDATORY):
If a tool parameter is optional and not needed for this call (e.g. ClusterHealthTool's
"index" when you want overall cluster health), OMIT that key from the arguments object
entirely. Never pass it as null or an empty string.

STRICT SCOPE RULE (MANDATORY):
You must ALWAYS attempt a tool call (ListIndexTool, IndexMappingTool, or SearchIndexTool)
before answering ANY factual question about the data itself -- even if you think you
already know the answer from your own training. NEVER answer a data question from your
own general knowledge.

META-QUESTION EXCEPTION: Questions about the tools themselves -- e.g. "what tools do
you have", "describe IndexMappingTool", "list all available tools" -- are NOT data
questions. You already know the full list of tools and their descriptions from your
own function/tool schema, so answer these directly without a tool call and without
using the "not available" fallback.

GIVE-UP RULE (MANDATORY):
Before searching for any single part of the user's question, first re-check the
AVAILABLE INDICES catalog below and identify the SINGLE most relevant index for
that part (e.g. a request for a service's link/URL maps to "services", NOT to
"applications" or "error_logs"). Search only that one index for that part.
If that search returns zero results, do NOT try additional indices for the same
term -- immediately conclude that data is unavailable for that specific part and
move on. Never repeat the same failed search pattern across multiple indices
"just in case." Never give up on a part of the question without having first
tried the ONE index the catalog identifies as correct for it.

MULTI-PART QUESTION RULE (MANDATORY):
If the user's question has multiple distinct parts (e.g. several counts, a definition,
and a list), identify all parts first, then answer each part with the minimum number
of tool calls needed. Do not spend extra rounds re-verifying parts you have already
answered successfully.

If the user's question is unrelated to the available indices, or if the tool
results contain no relevant information, you MUST respond with exactly:
"I'm sorry, this information is not available in this system's data."
Do NOT fill the gap using your own knowledge under any circumstances.

Never invent index names or fields. Always answer strictly in plain and professional English!.""" + INDEX_CATALOG_NOTE


def _allow_null_for_optional_params(schema: dict) -> dict:
    """FIX #4: optional parameters ke type mein 'null' bhi allow karta hai, taaki
    Groq ka strict schema validation LLM ke 'null' output ko reject na kare."""
    schema = json.loads(json.dumps(schema))
    required = set(schema.get("required", []))
    properties = schema.get("properties", {})
    for name, prop in properties.items():
        if name in required or not isinstance(prop, dict):
            continue
        t = prop.get("type")
        if isinstance(t, str) and t != "null":
            prop["type"] = [t, "null"]
        elif isinstance(t, list) and "null" not in t:
            t.append("null")
    return schema


def mcp_tool_to_groq_schema(tool) -> dict:
    """FastMCP tool definitions ko Groq LLM ke samajhne layak JSON Schema mein convert karta hai."""
    params = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", {"type": "object", "properties": {}})
    params = _allow_null_for_optional_params(params)
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": params,
        },
    }


def _extract_json_payload(text: str):
    """FIX #5: Kai MCP tools apna result ek text-label ke saath prefix karte hain
    (jaise "Indices in the cluster:\\n[...]"), pure JSON nahi. Ye function sabse
    pehla '[' ya '{' dhoondh kar wahin se JSON parse karta hai."""
    candidates = [i for i in (text.find('['), text.find('{')) if i != -1]
    if not candidates:
        raise ValueError("Tool result mein koi JSON payload nahi mila")
    start = min(candidates)
    return json.loads(text[start:])


# =====================================================================
# 3. THE GATEKEEPER (SECURITY INTERCEPTOR LOGIC)
# =====================================================================
def _extract_indices_from_msearch_body(body) -> list:
    """
    FIX #6: MsearchTool ke index names top-level 'index' argument mein nahi
    hote -- ye 'body' parameter ke andar hote hain, jo do formats mein aa
    sakta hai (opensearch-mcp-server-py ke official schema ke mutabik):

      1. JSON array: [header1, query1, header2, query2, ...]
         jahan har "header" object mein {"index": "some_index"} hota hai.
      2. NDJSON string: wahi header/query pairs, lekin newline-separated
         text lines ki tarah, ek dusre ke baad.

    Security-critical: agar body ko safely parse na kiya ja sake, ye
    function FAIL-CLOSED hota hai -- ek aisa sentinel index naam return
    karta hai jo kabhi bhi allowed_indices mein nahi hoga, taaki call
    default se BLOCK ho, allow nahi (fail-open galti se bachne ke liye).
    """
    try:
        if isinstance(body, str):
            lines = [line for line in body.strip().split("\n") if line.strip()]
            parsed_items = [json.loads(line) for line in lines]
        elif isinstance(body, list):
            parsed_items = body
        else:
            return ["__UNPARSEABLE_MSEARCH_BODY__"]

        indices = []
        for item in parsed_items:
            if isinstance(item, dict) and "index" in item:
                val = item["index"]
                if isinstance(val, list):
                    indices.extend(val)
                else:
                    indices.append(val)
        return indices
    except Exception:
        # Parse fail hua -- fail closed, allow nahi karna.
        return ["__UNPARSEABLE_MSEARCH_BODY__"]


def extract_requested_indices(fn_name: str, fn_args: dict) -> list:
    """LLM ke tool arguments mein se target index name ko extract karta hai."""
    for key in ("index", "indices", "index_name"):
        if key in fn_args:
            val = fn_args[key]
            return val if isinstance(val, list) else [val]

    # FIX #6: MsearchTool ke liye alag se 'body' ke andar dekhna padta hai.
    if fn_name == "MsearchTool" and "body" in fn_args:
        return _extract_indices_from_msearch_body(fn_args["body"])

    return []


def is_call_allowed(fn_name: str, fn_args: dict, allowed_indices: set, role: str) -> bool:
    """Deterministic Python-level security check. FIX #1: role ab explicit parameter hai."""
    if fn_name == "GenericOpenSearchApiTool":
        return False

    requested = extract_requested_indices(fn_name, fn_args)
    if not requested:
        return fn_name in get_safe_no_index_tools(role)

    return all(idx in allowed_indices for idx in requested)


# =====================================================================
# 4. MAIN EXECUTION & REASONING LOOP
# =====================================================================
async def run(question: str, role: str, is_verbose: bool, is_debug: bool):
    os_user = os.getenv("OPENSEARCH_ADMIN_USER", "admin")
    os_pass = os.getenv("OPENSEARCH_ADMIN_PASS")

    official_server_transport = StdioTransport(
        command="uvx",
        args=["opensearch-mcp-server-py"],
        env={
            "PATH": os.environ.get("PATH", ""),
            "OPENSEARCH_URL": f"http://{os.getenv('OPENSEARCH_HOST', 'localhost')}:{os.getenv('OPENSEARCH_PORT', '9201')}",
            "OPENSEARCH_USER": os_user,
            "OPENSEARCH_USERNAME": os_user,
            "OPENSEARCH_PASSWORD": os_pass,
            "OPENSEARCH_PASS": os_pass,
        },
    )

    groq = Groq(
        api_key=os.getenv("GROQ_API_KEY"),
        base_url=os.getenv("GROQ_BASE_URL", "https://api.groq.com").rstrip("/"),
        max_retries=int(os.getenv("GROQ_MAX_RETRIES", "2")),
        timeout=float(os.getenv("GROQ_TIMEOUT_SECONDS", "60")),
    )

    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    allowed_indices = CITIZEN_ALLOWED_INDICES if role == "citizen" else ADMIN_ALLOWED_INDICES
    allowed_str = ", ".join(sorted(allowed_indices))

    DYNAMIC_SYSTEM_PROMPT = BASE_SYSTEM_PROMPT + f"""

    ROLE RESTRICTION & SECURITY CLEARANCE:
    You are operating as role '{role.upper()}'. Only these indices are permitted for this role: {allowed_str}. 
    If the user's question requires data from a restricted index, do not attempt the query -- politely tell the user this data requires administrative access.
    """

    try:
        async with Client(official_server_transport) as mcp_client:
            all_tools = await mcp_client.list_tools()
            groq_tools = [mcp_tool_to_groq_schema(t) for t in all_tools]

            messages = [
                {"role": "system", "content": DYNAMIC_SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ]

            for round_num in range(10):
                response = groq.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=groq_tools,
                    tool_choice="auto",
                )
                msg = response.choices[0].message

                if not msg.tool_calls:
                    print("\n=== FINAL ANSWER ===")
                    print(msg.content)
                    return

                messages.append(msg)

                for call in msg.tool_calls:
                    fn_name = call.function.name
                    fn_args = json.loads(call.function.arguments or "{}")

                    # Debug visibility: raw args hamesha dikhte hain verbose mode mein,
                    # chahe call allow ho ya block -- taaki koi bhi Gatekeeper decision
                    # "why" ke saath traceable rahe.
                    if is_verbose:
                        print(f"\n🔍 [DEBUG] Tool: {fn_name}, Raw args: {json.dumps(fn_args, indent=2)}")

                    if not is_call_allowed(fn_name, fn_args, allowed_indices, role):
                        if is_verbose:
                            print(f"\n⚠️  [BLOCKED]: Security policy prevented '{role}' access to requested index.")

                        result_text = json.dumps({
                            "error": f"Access denied: Security policy prevents role '{role}' from querying this index or tool."
                        })
                    else:
                        if is_verbose:
                            print(f"\n🛠️  [TOOL CALLED]: {fn_name}")

                        try:
                            tool_result = await mcp_client.call_tool(fn_name, fn_args)
                            result_text = tool_result.content[0].text if tool_result.content else "[]"
                        except Exception as e:
                            result_text = json.dumps({
                                "error": f"Tool execution failed: {str(e)}",
                                "hint": "Check your query_dsl format and wrap inside a root 'query' object."
                            })

                    # FIX #5: _extract_json_payload() use kiya, seedha json.loads() nahi.
                    if fn_name == "ListIndexTool":
                        try:
                            indices_data = _extract_json_payload(result_text)
                            if isinstance(indices_data, list):
                                filtered_indices = [i for i in indices_data if i.get("index") in allowed_indices]
                                result_text = json.dumps(filtered_indices)
                            else:
                                result_text = json.dumps([])
                        except Exception:
                            result_text = json.dumps([])

                    if is_verbose:
                        print(f"📥 [RESULT]:\n{result_text[:400]}... (truncated)\n")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result_text,
                    })

            print("\n⚠️  Max reasoning rounds (10) reached without a final answer.")

    except Exception as e:
        sys.exit(f"ERROR: Could not complete request via OpenSearch MCP: {e}")


# =====================================================================
# 5. CLI ARGUMENT PARSER & ENTRY POINT
# =====================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Official OpenSearch MCP Client with Gatekeeper RBAC")
    parser.add_argument("question", type=str, help="User query for the RAG system")
    parser.add_argument("--role", choices=["citizen", "admin"], default="citizen", help="User role access level")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable structured execution logging")
    parser.add_argument("--debug", action="store_true", help="Enable raw debug mode")

    args = parser.parse_args()

    warnings.filterwarnings("ignore")

    noisy_libraries = [
        "opensearch", "opensearch.client", "opensearch.connection",
        "mcp.server.lowlevel.server", "mcp_server_opensearch",
        "httpx", "httpcore", "fastmcp", "root"
    ]

    logging.basicConfig(level=logging.ERROR)
    for lib in noisy_libraries:
        logging.getLogger(lib).setLevel(logging.ERROR)

    if args.verbose:
        print(f"==================================================")
        print(f"🏢 E-GOVERNANCE PORTAL - ROLE: {args.role.upper()}")
        print(f"==================================================")

    asyncio.run(run(args.question, args.role, args.verbose, args.debug))
