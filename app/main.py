import uuid
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from app.agent import run_agent_turn
from app.db import get_conn

app = FastAPI()
app.mount("/static", StaticFiles(directory="app/static"), name="static")

SESSIONS = {}  # session_id -> {"account_id": ..., "history": [...]}
VALID_ACCOUNTS = {"ACCT-001", "ACCT-002", "ACCT-003", "ACCT-004"}

@app.get("/")
def index():
    return FileResponse("app/static/index.html")

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