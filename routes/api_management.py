"""API Management - Token display, docs, live OTP feed"""
from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from database import get_db
from auth import verify_token, extract_token
from routes.deps import get_current_user, require_role
from fastapi import Depends
import secrets

router = APIRouter(prefix="/api/api-management", tags=["api-management"])

@router.get("/my-token")
async def get_my_token(request: Request, p=Depends(get_current_user)):
    with get_db() as conn:
        user = conn.execute("SELECT api_token FROM users WHERE id=?", (p["id"],)).fetchone()
        if not user: raise HTTPException(404, "User not found")
        token = user["api_token"]
        if not token:
            token = "sig_" + secrets.token_urlsafe(32)
            conn.execute("UPDATE users SET api_token=? WHERE id=?", (token, p["id"]))
    return {"token": token, "api_base": "/api/webhook/sms"}

@router.post("/regenerate-token")
async def regenerate_token(request: Request, p=Depends(get_current_user)):
    new_token = "sig_" + secrets.token_urlsafe(32)
    with get_db() as conn:
        conn.execute("UPDATE users SET api_token=? WHERE id=?", (new_token, p["id"]))
    return {"token": new_token, "message": "Token regenerated"}

@router.get("/admin/tokens")
async def list_all_tokens(request: Request, p=Depends(require_role(['admin', 'manager']))):
    with get_db() as conn:
        rows = conn.execute("SELECT u.id, u.username, u.role, u.status, u.api_token, u.balance, u.last_login, u.created_at FROM users u ORDER BY u.created_at DESC").fetchall()
    return {"data": [dict(r) for r in rows]}

@router.post("/admin/revoke-token/{user_id}")
async def revoke_token(request: Request, user_id: str, p=Depends(require_role(['admin', 'manager']))):
    with get_db() as conn:
        conn.execute("UPDATE users SET api_token=NULL WHERE id=?", (user_id,))
    return {"message": "Token revoked"}

@router.post("/admin/regenerate-token/{user_id}")
async def admin_regenerate(request: Request, user_id: str, p=Depends(require_role(['admin', 'manager']))):
    new_token = "sig_" + secrets.token_urlsafe(32)
    with get_db() as conn:
        conn.execute("UPDATE users SET api_token=? WHERE id=?", (new_token, user_id))
    return {"token": new_token, "message": "Token regenerated for user"}

@router.get("/docs")
async def api_docs(request: Request):
    return {
        "title": "SIGMAPANEL API",
        "base_url": "/api/webhook/sms",
        "auth_header": "Authorization: Bearer {token}",
        "endpoints": [
            {"method": "POST", "path": "/api/webhook/sms", "desc": "Receive SMS", "body": {"number": "+1234567890", "message": "Your code is 123456", "sender": "Google"}},
            {"method": "GET", "path": "/api/sms?page=1&limit=20", "desc": "List SMS messages"},
            {"method": "GET", "path": "/api/numbers?available=true", "desc": "List available numbers"},
        ]
    }

@router.get("/live-otp")
async def live_otp(request: Request, limit: int = Query(50, ge=1, le=200), p=Depends(get_current_user)):
    with get_db() as conn:
        if p["role"] in ("admin", "manager"):
            rows = conn.execute("SELECT s.number, s.sender, s.service, s.otp, s.message, s.received_at FROM sms_received s WHERE s.otp IS NOT NULL AND s.otp != '' ORDER BY s.received_at DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = conn.execute("SELECT s.number, s.sender, s.service, s.otp, s.message, s.received_at FROM sms_received s WHERE s.assigned_to=? AND s.otp IS NOT NULL AND s.otp != '' ORDER BY s.received_at DESC LIMIT ?", (p["username"], limit)).fetchall()
    return {"data": [dict(r) for r in rows]}
