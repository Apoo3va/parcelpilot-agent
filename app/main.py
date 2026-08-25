import uuid
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from app.agent import run_agent_turn
from app.db import get_conn
from app.tools import NOW

app = FastAPI()
app.mount("/static", StaticFiles(directory="app/static"), name="static")

SESSIONS = {}  # session_id -> {"account_id": ..., "history": [...]}
VALID_ACCOUNTS = {"ACCT-001", "ACCT-002", "ACCT-003", "ACCT-004"}

KNOWN_ISSUE_KEYWORDS = {
    "KI-208 (Bulk Upload failures)": ["bulk upload", "csv", "upload fail"],
    "KI-211 (SwiftShip webhook delay)": ["swiftship", "still shows booked", "webhook", "pickup"],
    "Security incident": ["api key", "security", "credential", "exposed", "breach"],
}

PLAN_P1_TARGET_MINUTES = {"Enterprise": 30, "Growth": 120, "Standard": 240}
PLAN_P2_TARGET_MINUTES = {"Enterprise": 120, "Growth": 240, "Standard": 1440}
PLAN_P3_TARGET_MINUTES = {"Enterprise": 1440, "Growth": 2880, "Standard": 2880}


def classify_severity(subject, description):
    text = f"{subject or ''} {description or ''}".lower()
    if any(k in text for k in ["api key", "security", "credential", "outage",
                                 "cannot create", "shipment creation is failing",
                                 "all shipment"]):
        return "P1"
    if any(k in text for k in ["still shows booked", "webhook", "bulk upload", "csv", "degraded"]):
        return "P2"
    return "P3"


@app.get("/")
def index():
    return FileResponse("app/static/index.html")


@app.get("/radar", include_in_schema=False)
def radar_page():
    return FileResponse("app/static/radar.html")


@app.post("/login")
async def login(req: Request):
    body = await req.json()
    account_id = body.get("account_id")  # or "INTERNAL"
    if account_id != "INTERNAL" and account_id not in VALID_ACCOUNTS:
        return JSONResponse({"error": "invalid account"}, status_code=400)
    session_id = str(uuid.uuid4())
    SESSIONS[session_id] = {
        "account_id": None if account_id == "INTERNAL" else account_id,
        "history": []
    }
    return {"session_id": session_id}


@app.post("/chat")
async def chat(req: Request):
    body = await req.json()
    session_id, message = body["session_id"], body["message"]
    session = SESSIONS.get(session_id)
    if not session:
        return JSONResponse({"error": "invalid session"}, status_code=401)
    text, trace = run_agent_turn(session_id, session["account_id"], session["history"], message)
    return {"reply": text, "tool_trace": trace}


@app.get("/accounts")
def list_accounts():
    conn = get_conn()
    rows = conn.execute("SELECT account_id, account_name FROM accounts").fetchall()
    return [dict(r) for r in rows]


@app.get("/radar-data")
def issue_radar_data():
    conn = get_conn()
    tickets = [dict(r) for r in conn.execute("SELECT * FROM tickets").fetchall()]
    accounts = {r["account_id"]: dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()}

    clusters = {name: [] for name in KNOWN_ISSUE_KEYWORDS}
    clusters["Uncategorized"] = []

    sla_flags = []

    for t in tickets:
        text = f"{t.get('subject','')} {t.get('description','')}".lower()
        matched = False
        for cluster_name, keywords in KNOWN_ISSUE_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                clusters[cluster_name].append(t)
                matched = True
                break
        if not matched:
            clusters["Uncategorized"].append(t)

        severity = classify_severity(t.get("subject"), t.get("description"))
        account = accounts.get(t["account_id"], {})
        plan = account.get("plan", "Standard")
        target_map = {"P1": PLAN_P1_TARGET_MINUTES, "P2": PLAN_P2_TARGET_MINUTES, "P3": PLAN_P3_TARGET_MINUTES}[severity]
        target_minutes = target_map.get(plan, 2880)

        try:
            created = datetime.strptime(t["created_at"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            try:
                created = datetime.strptime(t["created_at"], "%Y-%m-%d %H:%M")
            except Exception:
                created = NOW

        elapsed_minutes = (NOW - created).total_seconds() / 60
        risk_ratio = elapsed_minutes / target_minutes if target_minutes else 0

        if t.get("status", "").lower() not in ("closed", "resolved") and risk_ratio >= 0.75:
            sla_flags.append({
                "ticket_id": t["ticket_id"],
                "account_id": t["account_id"],
                "account_name": account.get("account_name", t["account_id"]),
                "subject": t.get("subject"),
                "inferred_severity": severity,
                "elapsed_minutes": round(elapsed_minutes, 1),
                "target_minutes": target_minutes,
                "status": "BREACHED" if risk_ratio >= 1.0 else "AT RISK",
            })

    cross_customer = []
    for name, items in clusters.items():
        if name == "Uncategorized":
            continue
        accts = set(i["account_id"] for i in items)
        if len(accts) >= 2:
            cross_customer.append({"cluster": name, "accounts": list(accts), "ticket_count": len(items)})

    return {
        "clusters": {k: v for k, v in clusters.items() if v},
        "sla_flags": sla_flags,
        "cross_customer_patterns": cross_customer,
    }