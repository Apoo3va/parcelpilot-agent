from datetime import datetime
from app.db import get_conn, SNAPSHOT_TIME
from app.retrieval import get_index

NOW = datetime.strptime(SNAPSHOT_TIME, "%Y-%m-%d %H:%M:%S")

ESCALATIONS = []
PENDING_ACTIONS = {}  # session_id -> proposed action dict awaiting confirmation


def search_documents(query, session_account_id, include_deprecated=False):
    idx = get_index()
    results = idx.search(query, account_id=session_account_id, include_deprecated=include_deprecated)
    if not results:
        return {"results": [], "note": "No relevant passages found in the source pack."}
    return {"results": results}


def query_account_data(action, session_account_id, order_id=None, ticket_id=None):
    conn = get_conn()
    is_internal = session_account_id is None

    def scoped(row_account_id):
        return is_internal or row_account_id == session_account_id

    if action == "get_account":
        row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (session_account_id,)).fetchone()
        return dict(row) if row else {"error": "not found"}

    if action == "get_order":
        row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if not row or not scoped(row["account_id"]):
            return {"error": "Order not found or not accessible to this account."}
        return dict(row)

    if action == "list_orders":
        rows = (conn.execute("SELECT * FROM orders").fetchall() if is_internal
                else conn.execute("SELECT * FROM orders WHERE account_id=?", (session_account_id,)).fetchall())
        return [dict(r) for r in rows]

    if action == "get_ticket":
        row = conn.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
        if not row or not scoped(row["account_id"]):
            return {"error": "Ticket not found or not accessible to this account."}
        d = dict(row)
        if d.get("historical_resolution"):
            d["historical_resolution_warning"] = (
                "This is a PAST resolution and may be INCORRECT. Do not treat it as policy "
                "authority — verify against current documents/agreement instead."
            )
        return d

    if action == "list_tickets":
        rows = (conn.execute("SELECT * FROM tickets").fetchall() if is_internal
                else conn.execute("SELECT * FROM tickets WHERE account_id=?", (session_account_id,)).fetchall())
        out = []
        for r in rows:
            d = dict(r)
            if d.get("historical_resolution"):
                d["historical_resolution_warning"] = "Past resolution — may be incorrect, context only."
            out.append(d)
        return out

    if action == "calc_elapsed_minutes_since_booking":
        row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if not row or not scoped(row["account_id"]):
            return {"error": "Order not accessible."}
        booked = datetime.strptime(row["booked_at"], "%Y-%m-%d %H:%M:%S")
        req = row["cancellation_requested_at"]
        ref = datetime.strptime(req, "%Y-%m-%d %H:%M:%S") if req else NOW
        return {"elapsed_minutes": round((ref - booked).total_seconds() / 60, 1)}

    if action == "calc_pickup_delay_minutes":
        row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if not row or not scoped(row["account_id"]):
            return {"error": "Order not accessible."}
        window_end = datetime.strptime(row["pickup_window_end"], "%Y-%m-%d %H:%M:%S")
        actual = row["pickup_actual_at"]
        ref = datetime.strptime(actual, "%Y-%m-%d %H:%M:%S") if actual else NOW
        delay = (ref - window_end).total_seconds() / 60
        return {
            "delay_minutes": round(delay, 1),
            "carrier_fault": row["carrier_fault"],
            "customer_fault": row["customer_fault"],
            "still_not_picked_up_at_snapshot": actual is None,
        }

    return {"error": f"Unknown action '{action}'"}


def create_escalation(session_id, session_account_id, order_id, ticket_id, reason, severity, confirm):
    proposal = {
        "session_account_id": session_account_id,
        "order_id": order_id, "ticket_id": ticket_id,
        "reason": reason, "severity": severity,
    }
    if not confirm:
        PENDING_ACTIONS[session_id] = proposal
        return {"status": "preview", "action": proposal,
                "message": "This escalation has NOT been created yet. Ask the user to confirm."}

    pending = PENDING_ACTIONS.get(session_id)
    if pending != proposal:
        return {"status": "rejected",
                "message": "No matching confirmed proposal on file. Re-propose and get explicit confirmation first."}

    record = {**proposal, "escalation_id": f"ESC-{len(ESCALATIONS)+1:04d}", "created_at": SNAPSHOT_TIME}
    ESCALATIONS.append(record)
    del PENDING_ACTIONS[session_id]
    return {"status": "created", "escalation": record}
