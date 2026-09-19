"""
=====================================================================
OFFICIAL OPENSEARCH MCP CLIENT WITH APPLICATION-LEVEL GATEKEEPER RBAC
=====================================================================
WHAT: Connects Groq LLM to the official opensearch-mcp-server-py package.
WHY: Implements a secure E-Governance RAG pipeline with role-based filtering.

NOTE: OpenSearch native security plugin is ENABLED (DISABLE_SECURITY_PLUGIN=false)
per sir's instruction. We keep the OpenSearch side simple with a SINGLE admin
credential. Role-based access (citizen vs admin) is enforced entirely at the
APPLICATION layer below via the gatekeeper (is_call_allowed / allowed_indices),
not via separate OpenSearch service accounts.
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
# Script start hote hi check karte hain ki API key available hai ya nahi.
# Agar missing ho, toh clean error message milta hai bina kisi confusing traceback ke.
if not os.getenv("GROQ_API_KEY"):
    sys.exit("ERROR: GROQ_API_KEY not set. Check your .env file.")

if not os.getenv("OPENSEARCH_ADMIN_PASS"):
    sys.exit("ERROR: OPENSEARCH_ADMIN_PASS not set. Check your .env file.")

# =====================================================================
# 1. ROLE-BASED ACCESS CONTROL (RBAC) CONFIGURATION
# =====================================================================
# Citizen role sirf public-facing data indices ko access kar sakta hai.
CITIZEN_ALLOWED_INDICES = {"schemes", "services", "public_guidelines", "touristplace"}

# Admin role ko citizen wale indices ke sath-sath internal logs aur applications ka bhi access milta hai.
ADMIN_ALLOWED_INDICES = CITIZEN_ALLOWED_INDICES | {"error_logs", "applications"}

# Aise tools jo koi specific index name argument nahi maangte (jaise ListIndexTool),
# unhe explicitly safe list mein rakha gaya hai taaki fail-closed policy maintain ho sake.
SAFE_NO_INDEX_TOOLS = {"ListIndexTool"}

# =====================================================================
# 2. PROMPT ENGINEERING & SYSTEM INSTRUCTIONS
# =====================================================================
# LLM ko strict instructions di jaati hain ki wo bina tool call kiye general knowledge se jawab na de.
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

STRICT SCOPE RULE (MANDATORY):
You must ALWAYS attempt a tool call (ListIndexTool, IndexMappingTool, or SearchIndexTool)
before answering ANY factual question -- even if you think you already know the answer
from your own training. NEVER answer from your own general knowledge.

If the user's question is unrelated to the available indices, or if the tool
results contain no relevant information, you MUST respond with exactly:
"Maaf kijiye, ye jaankari is system ke data me available me nahi hai."
Do NOT fill the gap using your own knowledge under any circumstances.

Never invent index names or fields. Always answer strictly in plain and professional English!."""

def mcp_tool_to_groq_schema(tool) -> dict:
    """
    FastMCP tool definitions ko Groq LLM ke samajhne layak JSON Schema format mein convert karta hai.
    """
    params = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", {"type": "object", "properties": {}})
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": params,
        },
    }

# =====================================================================
# 3. THE GATEKEEPER (SECURITY INTERCEPTOR LOGIC)
# =====================================================================
def extract_requested_indices(fn_name: str, fn_args: dict) -> list[str]:
    """LLM ke tool arguments mein se target index name ko extract karta hai."""
    for key in ("index", "indices", "index_name"):
        if key in fn_args:
            val = fn_args[key]
            return val if isinstance(val, list) else [val]
    return []

def is_call_allowed(fn_name: str, fn_args: dict, allowed_indices: set) -> bool:
    """
    Deterministic Python-level security check jo ensure karta hai ki 
    current role sirf authorized indices ko hi query kare.
    """
    # Raw API access tool ko completely block kiya gaya hai taaki security bypass na ho sake.
    if fn_name == "GenericOpenSearchApiTool":
        return False

    requested = extract_requested_indices(fn_name, fn_args)
    if not requested:
        return fn_name in SAFE_NO_INDEX_TOOLS

    return all(idx in allowed_indices for idx in requested)


# =====================================================================
# 4. MAIN EXECUTION & REASONING LOOP
# =====================================================================
async def run(question: str, role: str, is_verbose: bool, is_debug: bool):
    """
    Async function jo MCP server ke sath connection banata hai, 
    LLM ke sath chat loop handle karta hai, aur security gatekeeper ko enforce karta hai.
    """
    # OpenSearch native security ON hai, lekin simple rakha gaya hai: sirf EK admin
    # credential use hota hai OpenSearch se connect karne ke liye, chahe role
    # 'citizen' ho ya 'admin'. Actual RBAC neeche gatekeeper (is_call_allowed) karta hai.
    os_user = os.getenv("OPENSEARCH_ADMIN_USER", "admin")
    os_pass = os.getenv("OPENSEARCH_ADMIN_PASS")

    # StdioTransport ke zariye official MCP server ko secure environment variables ke sath spawn karna
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

    # Groq client initialization with robust retry and timeout settings
    groq = Groq(
        api_key=os.getenv("GROQ_API_KEY"),
        base_url=os.getenv("GROQ_BASE_URL", "https://api.groq.com").rstrip("/"),
        max_retries=int(os.getenv("GROQ_MAX_RETRIES", "2")),
        timeout=float(os.getenv("GROQ_TIMEOUT_SECONDS", "60")),
    )

    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    allowed_indices = CITIZEN_ALLOWED_INDICES if role == "citizen" else ADMIN_ALLOWED_INDICES
    allowed_str = ", ".join(sorted(allowed_indices))

    # Dynamic system prompt jisme current role ki permissions define hoti hain
    DYNAMIC_SYSTEM_PROMPT = BASE_SYSTEM_PROMPT + f"""

    ROLE RESTRICTION & SECURITY CLEARANCE:
    You are operating as role '{role.upper()}'. Only these indices are permitted for this role: {allowed_str}. 
    If the user's question requires data from a restricted index, do not attempt the query -- politely tell the user this data requires administrative access.
    """

    try:
        # MCP Client context manager ke sath connection establish karna
        async with Client(official_server_transport) as mcp_client:
            all_tools = await mcp_client.list_tools()
            groq_tools = [mcp_tool_to_groq_schema(t) for t in all_tools]

            messages = [
                {"role": "system", "content": DYNAMIC_SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ]

            # Multi-step reasoning loop (Max 10 rounds taaki infinite loop se bacha ja sake)
            for round_num in range(10):
                response = groq.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=groq_tools,
                    tool_choice="auto",
                )
                msg = response.choices[0].message

                # Agar model ne koi tool call nahi kiya, matlab final answer ready hai
                if not msg.tool_calls:
                    print("\n=== FINAL ANSWER ===")
                    print(msg.content)
                    return

                messages.append(msg)

                for call in msg.tool_calls:
                    fn_name = call.function.name
                    fn_args = json.loads(call.function.arguments or "{}")

                    # Gatekeeper check: Kya ye tool call aur index allowed hai?
                    if not is_call_allowed(fn_name, fn_args, allowed_indices):
                        if is_verbose:
                            print(f"\n⚠️  [BLOCKED]: Security policy prevented '{role}' access to requested index.")

                        result_text = json.dumps({
                            "error": f"Access denied: Security policy prevents role '{role}' from querying this index or tool."
                        })
                    else:
                        if is_verbose:
                            print(f"\n🛠️  [TOOL CALLED]: {fn_name}")
                            print(json.dumps(fn_args, indent=2))

                        try:
                            tool_result = await mcp_client.call_tool(fn_name, fn_args)
                            result_text = tool_result.content[0].text if tool_result.content else "[]"
                        except Exception as e:
                            # Self-healing error handling agar query DSL syntax mein koi galti ho
                            result_text = json.dumps({
                                "error": f"Tool execution failed: {str(e)}",
                                "hint": "Check your query_dsl format and wrap inside a root 'query' object."
                            })

                    # Defense in Depth: ListIndexTool ke output ko bhi role ke mutabik filter karna
                    if fn_name == "ListIndexTool":
                        try:
                            indices_data = json.loads(result_text)
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

    # Console logs ko clean rakhne ke liye noisy library logs ko suppress kiya gaya hai
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

    # Asynchronous execution loop start karna
    asyncio.run(run(args.question, args.role, args.verbose, args.debug))
