"""
Connects Groq (LLM) to the OFFICIAL opensearch-mcp-server-py package.
Implements Index-Level Role-Based Access Control (RBAC) via an Interceptor (Gatekeeper).
"""
import argparse
import asyncio
import json
import os
import logging
import warnings

from dotenv import load_dotenv
# fastmcp library MCP server se connect hone aur baat karne ke liye use hoti hai
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
# Groq official SDK (LLM ko call karne ke liye)
from groq import Groq

# .env file se OPENSEARCH_URL aur GROQ_API_KEY jaisi hidden settings load karta hai
load_dotenv()

# =====================================================================
# 1. SERVER SETUP
# =====================================================================
# Official server ko terminal command (uvx) ke through start karne ka setup.
# OPENSEARCH_NO_AUTH="true" rakha hai taaki POC mein password bypass ho sake.
OFFICIAL_SERVER_TRANSPORT = StdioTransport(
    command="uvx",
    args=["opensearch-mcp-server-py"],
    env={
        "OPENSEARCH_URL": os.getenv("OPENSEARCH_URL", "http://localhost:9201"),
        "OPENSEARCH_NO_AUTH": "true",
    },
)

# =====================================================================
# 2. ROLE-BASED ACCESS CONTROL (RBAC) RULES
# =====================================================================
# Yahan hum define kar rahe hain ki kis user role ko kaunse indices (tables) dekhne ki permission hai.
CITIZEN_ALLOWED_INDICES = {"schemes", "services", "public_guidelines"}
# Admin ko citizen wale + apne internal (error_logs, applications) dono dikhenge
ADMIN_ALLOWED_INDICES = CITIZEN_ALLOWED_INDICES | {"error_logs", "applications"}

# FIX: koi bhi tool jo index-parameter nahi maangta (jaise ListIndexTool),
# use explicitly whitelist karna padega -- warna default fail-CLOSED hoga.
# Naya tool add karne se pehle verify karo ki wo genuinely index-agnostic
# aur safe hai, tabhi yahan add karo.
SAFE_NO_INDEX_TOOLS = {"ListIndexTool"}

# =====================================================================
# 3. PROMPT ENGINEERING
# =====================================================================
# LLM ko batana padta hai ki OpenSearch ke official tools kaise use karne hain.

BASE_SYSTEM_PROMPT = """You are a helpful assistant with access to a live OpenSearch
cluster through generic OpenSearch MCP tools.

Unlike a domain-specific tool, SearchIndexTool expects a raw OpenSearch Query
DSL body. Before searching an unfamiliar index, first call IndexMappingTool to
learn its fields.

CRITICAL JSON SYNTAX RULE:
When using SearchIndexTool, your `query_dsl` parameter MUST always be properly wrapped in a root `"query"` object. 
Correct Example: {"query": {"match": {"field": "value"}}}

CRITICAL TOOL CALLING RULE:
Always use the EXACT tool name provided in the function list. Never append any extra characters, tags, or tokens (like <|channel|>commentary) to the tool name.

Never invent index names or fields. Always answer strictly! in plain and professional English!."""

def mcp_tool_to_groq_schema(tool) -> dict:
    """
    Kyunki FastMCP tools ka format thoda alag hota hai, ye function unhe 
    Groq LLM ke samajhne layak JSON Schema mein convert karta hai.
    """
    # Note: FastMCP SDK v2 ke baad inputSchema ko input_schema me badal diya gya hai.
    # Hum fallback de rahe hain taaki koi warning na aaye.
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
# 4. THE GATEKEEPER (INTERCEPTOR LOGIC) - SECURITY SYSTEM
# =====================================================================

def extract_requested_indices(fn_name: str, fn_args: dict) -> list[str]:
    """LLM jo index mang raha hai, usko parameters me se nikalna."""
    for key in ("index", "indices", "index_name"):
        if key in fn_args:
            val = fn_args[key]
            return val if isinstance(val, list) else [val]
    return []  # ListIndexTool jaise tools ke liye khali array


def is_call_allowed(fn_name: str, fn_args: dict, allowed_indices: set) -> bool:
    """Check karna ki kya role ko is index ki permission hai."""
    if fn_name == "GenericOpenSearchApiTool":
        return False

    requested = extract_requested_indices(fn_name, fn_args)
    if not requested:
        # FIX: FAIL-CLOSED, FAIL-OPEN NAHI.
        # Pehle yahan `return True` tha -- matlab koi bhi tool jo index
        # argument nahi maangta (jaise ClusterHealthTool, NodesInfoTool)
        # automatically allow ho jaata tha, chahe role citizen ho.
        # Ab sirf explicitly-verified safe tools hi bina-index allowed hain.
        return fn_name in SAFE_NO_INDEX_TOOLS

    return all(idx in allowed_indices for idx in requested)


# =====================================================================
# 5. MAIN EXECUTION LOOP
# =====================================================================
async def run(question: str, role: str, is_verbose: bool, is_debug: bool):
    groq = Groq(api_key=os.getenv("GROQ_API_KEY"))
    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    allowed_indices = CITIZEN_ALLOWED_INDICES if role == "citizen" else ADMIN_ALLOWED_INDICES
    allowed_str = ", ".join(sorted(allowed_indices))

    DYNAMIC_SYSTEM_PROMPT = BASE_SYSTEM_PROMPT + f"""

    ROLE RESTRICTION & SECURITY CLEARANCE:
    You are operating as role '{role.upper()}'. Only these indices are permitted for this role: {allowed_str}. 
    If the user's question requires data from a restricted index, do not attempt the query -- politely tell the user this data requires administrative access.
    """

    if is_debug:
        logging.info(f"Connecting via uvx... Role: {role.upper()}")

    async with Client(OFFICIAL_SERVER_TRANSPORT) as mcp_client:
        all_tools = await mcp_client.list_tools()
        groq_tools = [mcp_tool_to_groq_schema(t) for t in all_tools]

        if is_debug:
            print(f"\n[official OpenSearch MCP] Tools visible to LLM: {[t.name for t in all_tools]}\n")

        messages = [
            {"role": "system", "content": DYNAMIC_SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]

        for round_num in range(10):
            if is_debug:
                logging.debug(f"Starting LLM round {round_num + 1}...")

            response = groq.chat.completions.create(
                model=model,
                messages=messages,
                tools=groq_tools,
                tool_choice="auto",
            )
            msg = response.choices[0].message

            # Agar LLM ne koi tool call nahi kiya, matlab Final Answer ready hai.
            if not msg.tool_calls:
                print("\n=== FINAL ANSWER ===")
                print(msg.content)
                return

            messages.append(msg)

            for call in msg.tool_calls:
                fn_name = call.function.name
                fn_args = json.loads(call.function.arguments or "{}")

                # -------------------------------------------------------------
                # 🛡️ THE INTERCEPTOR (GATEKEEPER CHECK)
                # -------------------------------------------------------------
                if not is_call_allowed(fn_name, fn_args, allowed_indices):
                    if is_verbose or is_debug:
                        print(f"\n⚠️  [BLOCKED]: Security policy prevented '{role}' from accessing '{fn_args.get('index')}'")

                    result_text = json.dumps({
                        "error": f"Access denied: Security policy prevents role '{role}' from querying this index or tool."
                    })
                else:
                    # ---------------------------------------------------------
                    # 🛠️ STRUCTURED PRINTING FOR TOOL CALL (For Demo Mode)
                    # ---------------------------------------------------------
                    if is_verbose and not is_debug:
                        print(f"\n🛠️  [TOOL CALLED]: {fn_name}")
                        print(json.dumps(fn_args, indent=2))
                    elif is_debug:
                        print(f"-> Calling tool: {fn_name}({fn_args})")

                    try:
                        tool_result = await mcp_client.call_tool(fn_name, fn_args)
                        result_text = tool_result.content[0].text if tool_result.content else "[]"
                    except Exception as e:
                        # Self-healing logic for LLM syntax errors
                        error_msg = str(e)
                        if is_debug:
                            logging.warning(f"Tool execution error: {error_msg}")
                        result_text = json.dumps({
                            "error": f"Tool execution failed: {error_msg}",
                            "hint": "Did you forget to wrap your query_dsl inside a root 'query' object? Fix the JSON syntax and try again."
                        })

                # -------------------------------------------------------------
                # 🛡️ DEFENSE IN DEPTH: Filter ListIndexTool output
                # -------------------------------------------------------------
                if fn_name == "ListIndexTool":
                    try:
                        indices_data = json.loads(result_text)
                        if isinstance(indices_data, list):
                            filtered_indices = [i for i in indices_data if i.get("index") in allowed_indices]
                            result_text = json.dumps(filtered_indices)
                        else:
                            # FIX: response ek list nahi thi (unexpected shape) --
                            # fail-safe empty result do, raw/unfiltered data mat bhejo
                            result_text = json.dumps([])
                    except (json.JSONDecodeError, AttributeError, TypeError):
                        # FIX: pehle yahan silent `pass` tha, jisse unfiltered
                        # result_text as-is aage chala jaata tha (potential leak).
                        # Ab parsing fail hone par bhi fail-safe empty result.
                        result_text = json.dumps([])

                # -------------------------------------------------------------
                # 📥 STRUCTURED PRINTING FOR RESULTS
                # -------------------------------------------------------------
                if is_verbose and not is_debug:
                    try:
                        # Attempt to format JSON beautifully
                        parsed_json = json.loads(result_text)
                        formatted_result = json.dumps(parsed_json, indent=2)
                    except Exception:
                        formatted_result = result_text

                    # Print truncated beautiful result
                    print(f"📥 [RESULT]:\n{formatted_result[:500]}... (truncated)\n")
                elif is_debug:
                    print(f"<- Full Result: {result_text}\n")
                else:
                    print(f"<- Result: {result_text[:400]}... (truncated)\n")

                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result_text,
                })


# =====================================================================
# 6. SCRIPT ENTRY POINT (CLI CONFIGURATION)
# =====================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Official OpenSearch MCP Client with Gatekeeper RBAC")
    parser.add_argument("question", type=str)

    parser.add_argument("--role", choices=["citizen", "admin"], default="citizen", help="User role access level")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable structured clean demo logging")
    parser.add_argument("--debug", action="store_true", help="Enable raw internal deep debug logging")

    args = parser.parse_args()

    # -----------------------------------------------------------------
    # FORCE SILENCE NOISY LIBRARIES (Pydantic, httpx, opensearch)
    # -----------------------------------------------------------------
    warnings.filterwarnings("ignore")  # Ignore python warnings (like DeprecationWarning)

    noisy_libraries = [
        "opensearch", "opensearch.client", "opensearch.connection", 
        "mcp.server.lowlevel.server", "mcp_server_opensearch", 
        "httpx", "httpcore", "fastmcp", "root"
    ]

    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(asctime)s - %(name)s - %(message)s")
        print("🔧 Running in RAW DEBUG mode: Output will be very noisy.\n")
    else:
        # Normal or Verbose mode -> Silence everything else to ERROR level only
        logging.basicConfig(level=logging.ERROR)
        for lib in noisy_libraries:
            logging.getLogger(lib).setLevel(logging.ERROR)

        if args.verbose:
            print(f"==================================================")
            print(f"🏢 E-GOVERNANCE PORTAL - ROLE: {args.role.upper()}")
            print(f"==================================================")

    # Run the main async function
    asyncio.run(run(args.question, args.role, args.verbose, args.debug))
