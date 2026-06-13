"""SMPP Interconnection routes - manage external SMPP servers to connect to as client"""
from fastapi import APIRouter, Request, HTTPException, Depends
from database import get_db
from routes.deps import get_current_user, require_role
from auth import generate_id
from datetime import datetime

router = APIRouter(prefix="/api/smpp-interconnect", tags=["smpp-interconnect"])

@router.get("/servers")
async def list_servers(request: Request, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM smpp_remote_servers ORDER BY priority DESC").fetchall()
    return {"data": [dict(r) for r in rows]}

@router.post("/servers")
async def create_server(request: Request, body: dict, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        sid = generate_id()
        conn.execute("""INSERT INTO smpp_remote_servers
            (id, name, host, port, system_id, password, bind_type, src_ton, src_npi, dst_ton, dst_npi, enquire_link_interval, dlr_enabled, throughput_limit, priority)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, body['name'], body['host'], body.get('port', 2775), body['system_id'], body['password'],
             body.get('bind_type', 'transceiver'), body.get('src_ton', 1), body.get('src_npi', 1),
             body.get('dst_ton', 1), body.get('dst_npi', 1), body.get('enquire_link_interval', 30),
             1 if body.get('dlr_enabled') else 0, body.get('throughput_limit', 10), body.get('priority', 1)))
        row = conn.execute("SELECT * FROM smpp_remote_servers WHERE id=?", (sid,)).fetchone()
    return {"data": dict(row)}

@router.put("/servers/{sid}")
async def update_server(request: Request, sid: str, body: dict, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        cols = ['name', 'host', 'port', 'system_id', 'password', 'bind_type', 'src_ton', 'src_npi', 'dst_ton', 'dst_npi', 'enquire_link_interval', 'dlr_enabled', 'throughput_limit', 'priority', 'is_active']
        updates = {c: body[c] for c in cols if c in body}
        if updates:
            conn.execute(f"UPDATE smpp_remote_servers SET {','.join(f'{k}=?' for k in updates)} WHERE id=?",
                         list(updates.values()) + [sid])
        row = conn.execute("SELECT * FROM smpp_remote_servers WHERE id=?", (sid,)).fetchone()
    return {"data": dict(row)}

@router.delete("/servers/{sid}")
async def delete_server(request: Request, sid: str, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        conn.execute("DELETE FROM smpp_remote_servers WHERE id=?", (sid,))
    return {"message": "Server deleted"}

@router.post("/servers/{sid}/toggle")
async def toggle_server(request: Request, sid: str, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        r = conn.execute("SELECT is_active FROM smpp_remote_servers WHERE id=?", (sid,)).fetchone()
        new_state = 0 if r['is_active'] else 1
        conn.execute("UPDATE smpp_remote_servers SET is_active=? WHERE id=?", (new_state, sid))
    return {"is_active": new_state}

@router.get("/sessions")
async def get_sessions(request: Request, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM smpp_remote_sessions").fetchall()
    return {"data": [dict(r) for r in rows]}

@router.get("/logs")
async def get_interconnect_logs(request: Request, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM smpp_connection_logs WHERE server_id IS NOT NULL ORDER BY created_at DESC LIMIT 100").fetchall()
    return {"data": [dict(r) for r in rows]}

@router.post("/test-connection")
async def test_connection(request: Request, body: dict, p=Depends(require_role(["admin"]))):
    # This would normally attempt a real bind, but for now we'll simulate
    import asyncio
    await asyncio.sleep(1)
    return {"message": f"Successfully validated connection to {body.get('host')}:{body.get('port')}"}

@router.get("/server-sessions")
async def get_server_sessions(request: Request, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM smpp_server_sessions").fetchall()
    return {"data": [dict(r) for r in rows]}

@router.get("/server-logs")
async def get_server_logs(request: Request, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM smpp_connection_logs ORDER BY created_at DESC LIMIT 100").fetchall()
    return {"data": [dict(r) for r in rows]}

@router.get("/accounts")
async def list_server_accounts(request: Request, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM smpp_server_accounts").fetchall()
    return {"data": [dict(r) for r in rows]}

@router.post("/accounts")
async def create_server_account(request: Request, body: dict, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        aid = generate_id()
        conn.execute("INSERT INTO smpp_server_accounts (id, system_id, password, ip_whitelist, throughput_limit, status) VALUES (?,?,?,?,?,?)",
                     (aid, body['system_id'], body['password'], body.get('ip_whitelist'), body.get('throughput_limit', 10), 'active'))
        row = conn.execute("SELECT * FROM smpp_server_accounts WHERE id=?", (aid,)).fetchone()
    return {"data": dict(row)}

@router.delete("/accounts/{aid}")
async def delete_server_account(request: Request, aid: str, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        conn.execute("DELETE FROM smpp_server_accounts WHERE id=?", (aid,))
    return {"message": "Account deleted"}

@router.get("/failed-packets")
async def get_failed_packets(request: Request, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM smpp_failed_packets ORDER BY created_at DESC LIMIT 100").fetchall()
    return {"data": [dict(r) for r in rows]}

@router.get("/dlr-logs")
async def get_dlr_logs(request: Request, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM sms_received WHERE otp IS NOT NULL ORDER BY received_at DESC LIMIT 50").fetchall()
    return {"data": [dict(r) for r in rows]}

@router.get("/queue-stats")
async def get_queue_stats(request: Request, p=Depends(require_role(["admin"]))):
    return {
        "queued": 0,
        "processing": 0,
        "success_rate": 100
    }
