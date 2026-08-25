import os, json
import google.generativeai as genai
from dotenv import load_dotenv
from app.tools import search_documents, query_account_data, create_escalation

load_dotenv()
genai.configure(api_key=os.environ["GEMINI_API_KEY"])

MODEL_NAME = "gemini-3.6-flash"

TOOLS = [
    {
        "function_declarations": [
            {
                "name": "search_documents",
                "description": "Search ParcelPilot policies, SOPs, product docs, and customer agreements. Excludes the deprecated policy by default.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "include_deprecated": {
                            "type": "boolean",
                            "description": "Only true if explicitly comparing against old/historical policy."
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "query_account_data",
                "description": "Look up or calculate order/ticket/account data. Automatically scoped to the caller's own account.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["get_account", "get_order", "list_orders", "get_ticket",
                                     "list_tickets", "calc_elapsed_minutes_since_booking",
                                     "calc_pickup_delay_minutes"]
                        },
                        "order_id": {"type": "string"},
                        "ticket_id": {"type": "string"}
                    },
                    "required": ["action"]
                }
            },
            {
                "name": "create_escalation",
                "description": "Propose or create an escalation. ALWAYS call with confirm=false first to preview; only call with confirm=true after the user has explicitly confirmed in a follow-up message.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string"},
                        "ticket_id": {"type": "string"},
                        "reason": {"type": "string"},
                        "severity": {"type": "string", "enum": ["P1", "P2", "P3"]},
                        "confirm": {"type": "boolean"}
                    },
                    "required": ["reason", "severity", "confirm"]
                }
            }
        ]
    }
]

SYSTEM_PROMPT = """You are ParcelPilot's customer support assistant.

Source authority, highest to lowest:
1. The customer's own signed agreement (if one exists for their account).
2. Current policy (Support Policy v3), current SOP (Cancellation & Service Credit SOP v4), and current Product Operations Guide.
3. The deprecated Support Policy v2, never use this as current authority; only reference it if the user explicitly asks about old or historical policy.
4. Historical ticket resolutions, context only. They may be WRONG. Never cite them as policy authority. Always verify against current documents or the account's agreement instead.

When sources conflict, state the conflict briefly and follow the higher-authority source.

Before concluding a pickup didn't happen, consider known carrier or webhook delays if relevant (check the product operations and known issues content).

Always cite which source you relied on. If you cannot answer confidently, say so plainly and offer to escalate instead of guessing.

For escalations: always call create_escalation with confirm=false first to preview it, tell the user what you're proposing, and only call it again with confirm=true after they explicitly confirm in their next message. Never create an escalation without that explicit confirmation.

Do not reveal or reference data belonging to any account other than the one you're currently serving.

Do not use em dashes (—) or en dashes (–) in your responses. Use commas, periods, or colons instead.
"""

def _to_json_safe(args):
    """Gemini function-call args come back as a protobuf Map; convert to plain dict."""
    return {k: v for k, v in args.items()}

def run_agent_turn(session_id, account_id, history, user_message):
    model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        tools=TOOLS,
        system_instruction=SYSTEM_PROMPT
    )

    history.append({"role": "user", "parts": [{"text": user_message}]})
    tool_trace = []

    while True:
        response = model.generate_content(contents=history)
        candidate_parts = response.candidates[0].content.parts

        function_calls = [p.function_call for p in candidate_parts if p.function_call]

        # store model's turn (including any function_call parts) back into history
        history.append({"role": "model", "parts": candidate_parts})

        if not function_calls:
            final_text = "".join(p.text for p in candidate_parts if hasattr(p, "text") and p.text)
            return final_text, tool_trace

        response_parts = []
        for fc in function_calls:
            name = fc.name
            args = _to_json_safe(fc.args)

            if name == "search_documents":
                out = search_documents(args.get("query"), account_id, args.get("include_deprecated", False))
            elif name == "query_account_data":
                out = query_account_data(args.get("action"), account_id, args.get("order_id"), args.get("ticket_id"))
            elif name == "create_escalation":
                out = create_escalation(session_id, account_id, args.get("order_id"), args.get("ticket_id"),
                                         args.get("reason"), args.get("severity"), args.get("confirm", False))
            else:
                out = {"error": "unknown tool"}

            tool_trace.append({"tool": name, "input": args, "output": out})
            response_parts.append({
                "function_response": {
                    "name": name,
                    "response": {"result": json.loads(json.dumps(out, default=str))}
                }
            })

        history.append({"role": "user", "parts": response_parts})