"""
Connects Groq (LLM) to the custom OpenSearch MCP server (mcp_server.py) over
stdio and runs a natural-language query end-to-end:

    User question -> Groq (decides which tool to call) -> MCP server ->
    OpenSearch -> result -> Groq -> final natural-language answer

Usage:
    python groq_client.py --role citizen "Aadhaar update ke liye guidelines do"
    python groq_client.py --role admin --debug "Payment failure se jude error logs nikaalo"
    python groq_client.py --role admin -v "How many applications were submitted today?"
"""

import argparse
import asyncio
import json
import logging
import os

from dotenv import load_dotenv
from fastmcp import Client  # FastMCP client -- MCP server se connect/talk karne ke liye
from groq import Groq       # Groq LLM ka official Python SDK

# .env file se GROQ_API_KEY aur baaki environment variables load karta hai
load_dotenv()

# Citizen role ko sirf ye 5 "safe" tools dikhenge (public data wale)
CITIZEN_TOOLS = {
    "search_government_schemes",
    "get_new_schemes",
    "get_popular_services",
    "get_service_guidelines",
    "track_application_status",
}

# Ye 2 tools sirf admin role ke liye hain (internal/sensitive data wale)
ADMIN_ONLY_TOOLS = {
    "fetch_system_error_logs",
    "get_transaction_counts",
    "get_application_status_count"
}

# LLM ko diya jaane wala instruction -- kaise behave karna hai
SYSTEM_PROMPT = """You are a helpful assistant for the india.gov.in citizen/admin portal.
Always use the provided tools to answer questions about schemes, services,
applications, or system logs -- never invent scheme names, statistics, or
URLs from memory. If a tool returns no results, say so plainly.
Always answer in plain English, regardless of what language or script the
user's question is written in (English, Hindi, or Hinglish).
Keep answers concise and cite the url_link field when relevant.

CRITICAL: Only state facts that appear verbatim or as a direct paraphrase of
the tool's returned data. Do NOT add details, examples, or steps from your
own general knowledge (e.g. real-world document lists, portal names, or
procedures), even if they seem helpful or accurate. If the tool's data is
incomplete, say what's missing instead of filling the gap yourself."""

def mcp_tool_to_groq_schema(tool) -> dict:
    """Convert a FastMCP tool definition into the JSON schema Groq expects."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema or {"type": "object", "properties": {}},
        },
    }


async def run(question: str, role: str):
    """Main execution loop for processing user questions via Groq and MCP."""
    logging.info(f"Initializing Groq client for role: {role}")
    
    # Groq client banaya, .env se API key li
    groq = Groq(api_key=os.getenv("GROQ_API_KEY"))
    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    logging.debug(f"using model: {model}")

    # mcp_server.py ko subprocess ki tarah start karta hai aur connect hota hai (stdio transport)
    async with Client("mcp_server.py") as mcp_client:
        logging.debug("Connected to MCP server successfully.")
        
        # MCP server par jitne bhi tools register hain, unki full list mangwao
        all_tools = await mcp_client.list_tools()

        # ROLE-GATING: role ke hisaab se decide karo konse tools allowed hain
        allowed = CITIZEN_TOOLS if role == "citizen" else (CITIZEN_TOOLS | ADMIN_ONLY_TOOLS)
        
        # Sirf allowed wale tools hi LLM ko dikhenge, baaki filter ho jaayenge
        visible_tools = [t for t in all_tools if t.name in allowed]
        groq_tools = [mcp_tool_to_groq_schema(t) for t in visible_tools]

        print(f"\n[role={role}] Tools visible to LLM: {[t.name for t in visible_tools]}\n")
        logging.debug(f"Filtered {len(visible_tools)} tools out of {len(all_tools)} total tools.")

        # Conversation history -- system prompt + user ka sawal
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]

        # Tool-calling loop: allow up to a few rounds for multi-step queries
        for round_num in range(4):
            logging.debug(f"Starting tool-calling round {round_num + 1}...")
            
            # Groq ko messages + available tools bhejo
            response = groq.chat.completions.create(
                model=model,
                messages=messages,
                tools=groq_tools,
                tool_choice="auto",
            )
            msg = response.choices[0].message

            # Agar LLM ne koi tool call nahi kiya, matlab final answer ready hai
            if not msg.tool_calls:
                print("=== FINAL ANSWER ===")
                print(msg.content)
                return

            # LLM ka response history mein add karo
            messages.append(msg)
            
            # LLM ne jo tools call kiye hain unko process karo
            for call in msg.tool_calls:
                fn_name = call.function.name
                fn_args = json.loads(call.function.arguments or "{}")

                # SECURITY CHECK: agar tool allowed list mein nahi hai to error do
                if fn_name not in allowed:
                    logging.warning(f"Unauthorized tool call attempted: {fn_name}")
                    result_text = json.dumps({"error": "Tool not permitted for this role."})
                else:
                    print(f"-> Calling tool: {fn_name}({fn_args})")
                    logging.info(f"Executing tool {fn_name} with arguments {fn_args}")
                    
                    # Actual tool call MCP server ko jaata hai (OpenSearch query execute hoti hai)
                    tool_result = await mcp_client.call_tool(fn_name, fn_args)
                    result_text = tool_result.content[0].text if tool_result.content else "[]"
                    
                    print(f"<- Result: {result_text[:300]}\n")
                    logging.debug(f"Tool response received: {len(result_text)} bytes")

                # Tool ka result wapas conversation history mein daalo
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result_text,
                })

        print("=== FINAL ANSWER (after max tool rounds) ===")
        print(messages[-1])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Groq MCP Client with Role-Gating and Logging")
    parser.add_argument("question", type=str, help="The natural language question to process")
    parser.add_argument("--role", choices=["citizen", "admin"], default="citizen", help="User role access level")
    
    # Logging flags support --verbose (-v) aur --debug
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose info logging")
    parser.add_argument("--debug", action="store_true", help="Enable deep debug logging")
    
    args = parser.parse_args()

    # Logging level configuration based on flags passed in terminal
    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(asctime)s - %(levelname)s - %(message)s")
    elif args.verbose:
        logging.basicConfig(level=logging.INFO, format="[INFO] %(asctime)s - %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    # Async function ko run karne ka tareeka
    asyncio.run(run(args.question, args.role))
