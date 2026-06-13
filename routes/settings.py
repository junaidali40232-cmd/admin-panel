"""Settings routes - webhook config, system IP/port, user preferences"""
from fastapi import APIRouter, Request, HTTPException, Query, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import os, socket
from database import get_db
from auth import verify_token, extract_token, generate_id
from routes.deps import get_current_user

router = APIRouter(prefix="/api/settings", tags=["settings"])

class SettingCreate(BaseModel):
    key: str
    value: str
    userId: Optional[str] = None

@router.get("")
async def list_settings(request: Request, key: str = Query(None)):
    # Public settings allowed without auth for login/signup pages
    PUBLIC_KEYS = {'signup_enabled', 'contact_whatsapp', 'contact_teams', 'contact_telegram'}

    if key in PUBLIC_KEYS:
        # Allow access to specific public keys
        pass
    else:
        # Require auth for everything else
        get_current_user(request)

    conds, params = [], []
    # If authenticated and not admin, scope to self
    auth_header = request.headers.get("Authorization")
    if auth_header:
        tok = extract_token(auth_header)
        p = verify_token(tok) if tok else None
        if p and p["role"] != "admin":
            conds.append("(user_id IS NULL OR user_id = ?)"); params.append(p["id"])
    else:
        # Non-authenticated only sees global settings
        conds.append("user_id IS NULL")
    if key:
        conds.append("setting_key = ?"); params.append(key)
    where = " AND ".join(conds) if conds else "1=1"
    with get_db() as conn:
        rows = conn.execute(f"SELECT * FROM settings WHERE {where} ORDER BY setting_key", params).fetchall()
    return {"data": [dict(r) for r in rows]}

@router.post("")
async def upsert_setting(request: Request, body: SettingCreate, p=Depends(get_current_user)):
    uid = body.userId if p["role"] == "admin" else p["id"]
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM settings WHERE setting_key=? AND (user_id=? OR (user_id IS NULL AND ? IS NULL))",
            (body.key, uid, uid),
        ).fetchone()
        if existing:
            conn.execute("UPDATE settings SET setting_value=? WHERE id=?", (body.value, existing["id"]))
            row = conn.execute("SELECT * FROM settings WHERE id=?", (existing["id"],)).fetchone()
        else:
            sid = generate_id()
            conn.execute("INSERT INTO settings (id,setting_key,setting_value,user_id) VALUES (?,?,?,?)",
                         (sid, body.key, body.value, uid))
            row = conn.execute("SELECT * FROM settings WHERE id=?", (sid,)).fetchone()
    return {"data": dict(row)}

@router.get("/webhook-info")
async def webhook_info(request: Request, p=Depends(get_current_user)):
    """Returns server IP, port and webhook URL — shown in settings page"""
    host = request.headers.get("host", "")
    # Detect public IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        server_ip = s.getsockname()[0]
        s.close()
    except Exception:
        server_ip = "127.0.0.1"

    port = os.environ.get("PORT", "8000")
    scheme = "https" if request.headers.get("x-forwarded-proto") == "https" else "http"

    # Use the actual host from request if available (e.g. Railway domain)
    if host and "." in host:
        base_url = f"{scheme}://{host}"
    else:
        base_url = f"http://{server_ip}:{port}"

    return {
        "serverIp": server_ip,
        "port": port,
        "baseUrl": base_url,
        "webhookUrl": f"{base_url}/api/webhook/sms",
        "method": "POST",
        "contentType": "application/json",
        "fields": {
            "to":   "Destination number (international format: +525529001312)",
            "from": "Service name / sender",
            "msg":  "Message body",
            "uuid": "Unique message ID",
        },
        "example": {
            "to": "+525529001312",
            "from": "AmericanExpress",
            "msg": "Your OTP is 847291",
            "uuid": "msg-204953",
        },
    }
