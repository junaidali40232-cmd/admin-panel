"""Notifications - hierarchical visibility by role"""
from routes.deps import get_current_user, require_role
from fastapi import Depends, APIRouter, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
from database import get_db
from auth import verify_token, extract_token, generate_id

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

class NotifCreate(BaseModel):
    title: str
    message: str
    type: Optional[str] = "info"
    targetRole: Optional[str] = None

@router.get("")
async def list_notifications(request: Request, unread_only: bool = Query(False), p=Depends(get_current_user)):
    role = p["role"]
    uid  = p["id"]
    with get_db() as conn:
        if role in ("admin", "manager"):
            rows = conn.execute(
                """SELECT * FROM notifications
                   WHERE created_by_role IN ('admin','manager') OR target_role='reseller'
                   ORDER BY created_at DESC LIMIT 200"""
            ).fetchall()
        elif role == "reseller":
            rows = conn.execute(
                """SELECT * FROM notifications
                   WHERE (created_by_role IN ('admin','manager') AND (target_role IS NULL OR target_role='reseller'))
                      OR (created_by=? AND target_role IN ('sub_reseller'))
                   ORDER BY created_at DESC LIMIT 200""",
                (uid,)
            ).fetchall()
        else:
            me = conn.execute("SELECT parent_id FROM users WHERE id=?", (uid,)).fetchone()
            parent_id = me["parent_id"] if me else ""
            rows = conn.execute(
                """SELECT * FROM notifications
                   WHERE created_by=? AND target_role IN ('sub_reseller')
                   ORDER BY created_at DESC LIMIT 200""",
                (parent_id or "",)
            ).fetchall()
        read_ids = {r["notification_id"] for r in conn.execute(
            "SELECT notification_id FROM notification_reads WHERE user_id=?", (uid,)
        ).fetchall()} if rows else set()
    data = []
    for r in rows:
        d = dict(r)
        d["is_read"] = d["id"] in read_ids
        if unread_only and d["is_read"]: continue
        data.append(d)
    return {"data": data, "unread_count": sum(1 for d in data if not d["is_read"])}

@router.post("")
async def create_notification(request: Request, body: NotifCreate, p=Depends(get_current_user)):
    role = p["role"]
    if role == "admin":       target = body.targetRole or "reseller"
    elif role == "manager":   target = "reseller"
    elif role == "reseller":  target = body.targetRole if body.targetRole in ("sub_reseller") else "sub_reseller"
    else: raise HTTPException(403, "Not authorized to send notifications")
    with get_db() as conn:
        nid = generate_id()
        conn.execute(
            "INSERT INTO notifications (id,title,message,type,target_role,created_by,created_by_role) VALUES (?,?,?,?,?,?,?)",
            (nid, body.title, body.message, body.type, target, p["id"], role)
        )
        row = conn.execute("SELECT * FROM notifications WHERE id=?", (nid,)).fetchone()
    return JSONResponse(status_code=201, content={"data": dict(row)})

@router.post("/{nid}/read")
async def mark_read(request: Request, nid: str, p=Depends(get_current_user)):
    with get_db() as conn:
        try:
            conn.execute("INSERT INTO notification_reads (id,notification_id,user_id) VALUES (?,?,?)",
                         (generate_id(), nid, p["id"]))
        except Exception: pass
    return {"message": "Marked as read"}

@router.post("/mark-all-read")
async def mark_all_read(request: Request, p=Depends(get_current_user)):
    with get_db() as conn:
        notifs = conn.execute("SELECT id FROM notifications").fetchall()
        for n in notifs:
            try:
                conn.execute("INSERT INTO notification_reads (id,notification_id,user_id) VALUES (?,?,?)",
                             (generate_id(), n["id"], p["id"]))
            except Exception: pass
    return {"message": "All marked as read"}

@router.delete("/{nid}")
async def delete_notification(request: Request, nid: str, p=Depends(get_current_user)):
    if p["role"] not in ("admin","manager","reseller"): raise HTTPException(403, "Not authorized")
    with get_db() as conn:
        conn.execute("DELETE FROM notification_reads WHERE notification_id=?", (nid,))
        conn.execute("DELETE FROM notifications WHERE id=?", (nid,))
    return {"message": "Deleted"}
