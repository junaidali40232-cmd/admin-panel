"""Security and Firewall Management routes"""
from fastapi import APIRouter, Request, HTTPException, Query, Depends
from database import get_db
from routes.deps import get_current_user, require_role
from auth import generate_id
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta

router = APIRouter(prefix="/api/security", tags=["security"])

class BlockIPRequest(BaseModel):
    ip: str
    reason: Optional[str] = "Manual block"
    days: Optional[int] = 30

@router.get("/events")
async def list_security_events(request: Request, limit: int = Query(50, ge=1, le=100), p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM security_events ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return {"data": [dict(r) for r in rows]}

@router.get("/blocked-ips")
async def list_blocked_ips(request: Request, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM blocked_ips ORDER BY created_at DESC").fetchall()
    return {"data": [dict(r) for r in rows]}

@router.post("/block-ip")
async def block_ip(request: Request, body: BlockIPRequest, p=Depends(require_role(["admin"]))):
    expires = (datetime.utcnow() + timedelta(days=body.days)).isoformat()
    with get_db() as conn:
        bid = generate_id()
        conn.execute("INSERT OR REPLACE INTO blocked_ips (id, ip_address, reason, expires_at) VALUES (?, ?, ?, ?)",
                     (bid, body.ip, body.reason, expires))
    return {"message": f"IP {body.ip} blocked until {expires}"}

@router.post("/unblock-ip/{ip}")
async def unblock_ip(request: Request, ip: str, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        conn.execute("DELETE FROM blocked_ips WHERE ip_address = ?", (ip,))
    return {"message": f"IP {ip} unblocked"}

@router.get("/stats")
async def security_stats(request: Request, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        blocked_count = conn.execute("SELECT COUNT(*) FROM blocked_ips").fetchone()[0]
        event_count = conn.execute("SELECT COUNT(*) FROM security_events WHERE created_at >= ?",
                                   ((datetime.utcnow() - timedelta(days=1)).isoformat(),)).fetchone()[0]
    return {
        "threat_score": "LOW",
        "blocked_ips": blocked_count,
        "recent_events": event_count
    }
