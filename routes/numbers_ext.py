"""Extended numbers routes: bulk import, assign range, return numbers"""
from fastapi import APIRouter, Request, HTTPException, Depends
from routes.deps import get_current_user, require_role
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List
from database import get_db
from auth import verify_token, extract_token, generate_id
from datetime import datetime
import re

router = APIRouter(prefix="/api/numbers-ext", tags=["numbers-ext"])

class BulkImport(BaseModel):
    numbersText: str           # one number per line
    country: str
    countryName: Optional[str] = None
    rangeName: Optional[str] = None
    rangeId: Optional[str] = None
    rate: Optional[float] = 0.0
    profitMargin: Optional[float] = 50.0

class AssignRange(BaseModel):
    rangeName: str
    username: str

class ReturnNumbers(BaseModel):
    username: Optional[str] = None
    rangeName: Optional[str] = None
    numberIds: Optional[List[str]] = None

class AllocateNumbers(BaseModel):
    rangeName: str
    quantity: int
    duration: str = "monthly"    # weekly | monthly | yearly | custom
    customDays: Optional[int] = None

class BulkAllocateRequest(BaseModel):
    userId: str
    rangeName: str
    quantity: int

class BulkRevokeRequest(BaseModel):
    scope: str # "global" | "user" | "range"
    userId: Optional[str] = None
    rangeName: Optional[str] = None

@router.post("/bulk-import")
async def bulk_import(request: Request, body: BulkImport, p=Depends(require_role(["admin", "manager"]))):
    lines = [l.strip() for l in body.numbersText.splitlines() if l.strip()]
    success, skipped, errors = 0, 0, []
    added_numbers = []
    with get_db() as conn:
        range_prefix = None
        if body.rangeId:
            rng = conn.execute("SELECT number_prefix FROM ranges WHERE id=?", (body.rangeId,)).fetchone()
            if rng and rng['number_prefix']:
                range_prefix = rng['number_prefix']
        for line in lines:
            num = re.sub(r'[\s\-\(\)]', '', line)
            if not num: continue
            if not num.startswith('+'): num = '+' + num
            if range_prefix and not num.startswith(range_prefix):
                errors.append(f"{num}: does not match range prefix '{range_prefix}'")
                continue
            if conn.execute("SELECT id FROM numbers WHERE number=?", (num,)).fetchone():
                skipped += 1; continue
            try:
                conn.execute("""INSERT INTO numbers (id,number,country,country_name,range_name,range_id,rate,profit_margin,status,total_sms)
                                VALUES (?,?,?,?,?,?,?,?,'active',0)""",
                             (generate_id(), num, body.country, body.countryName, body.rangeName, body.rangeId, body.rate, body.profitMargin))
                success += 1
                added_numbers.append(num)
            except Exception as e:
                errors.append(f"{num}: {e}")
        if body.rangeId:
            conn.execute("UPDATE ranges SET total_numbers=(SELECT COUNT(*) FROM numbers WHERE range_id=?) WHERE id=?",
                         (body.rangeId, body.rangeId))
    return {"success": success, "skipped": skipped, "errors": errors[:20], "total": len(lines), "added_numbers": added_numbers}

@router.post("/assign-range")
async def assign_range(request: Request, body: AssignRange, p=Depends(require_role(["admin", "manager"]))):
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        nums = conn.execute("SELECT id FROM numbers WHERE range_name=? AND (assigned_to IS NULL OR assigned_to='')",
                             (body.rangeName,)).fetchall()
        count = 0
        for n in nums:
            conn.execute("UPDATE numbers SET assigned_to=?, assigned_at=? WHERE id=?",
                         (body.username, now, n['id']))
            count += 1
    return {"assigned": count, "to": body.username}

@router.post("/return-numbers")
async def return_numbers(request: Request, body: ReturnNumbers, p=Depends(require_role(["admin", "manager"]))):
    with get_db() as conn:
        if body.numberIds:
            q = ",".join("?" * len(body.numberIds))
            conn.execute(f"UPDATE numbers SET assigned_to=NULL,assigned_at=NULL WHERE id IN ({q})", body.numberIds)
            count = len(body.numberIds)
        elif body.username and body.rangeName:
            r = conn.execute("UPDATE numbers SET assigned_to=NULL,assigned_at=NULL WHERE assigned_to=? AND range_name=?",
                              (body.username, body.rangeName))
            count = r.rowcount
        elif body.username:
            r = conn.execute("UPDATE numbers SET assigned_to=NULL,assigned_at=NULL WHERE assigned_to=?", (body.username,))
            count = r.rowcount
        elif body.rangeName:
            r = conn.execute("UPDATE numbers SET assigned_to=NULL,assigned_at=NULL WHERE range_name=?", (body.rangeName,))
            count = r.rowcount
        else:
            raise HTTPException(400, "Specify username, rangeName, or numberIds")
    return {"returned": count}

@router.post("/bulk-allocate")
async def bulk_allocate(request: Request, body: BulkAllocateRequest, p=Depends(require_role(["admin", "manager"]))):
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        user = conn.execute("SELECT username FROM users WHERE id=?", (body.userId,)).fetchone()
        if not user: raise HTTPException(404, "User not found")
        username = user['username']

        available = conn.execute("""SELECT id, number FROM numbers
                                   WHERE range_name=? AND (assigned_to IS NULL OR assigned_to='')
                                   LIMIT ?""", (body.rangeName, body.quantity)).fetchall()

        if len(available) < body.quantity:
            raise HTTPException(400, f"Only {len(available)} numbers available")

        for n in available:
            conn.execute("UPDATE numbers SET assigned_to=?, assigned_at=? WHERE id=?",
                         (username, now, n['id']))

        conn.execute("UPDATE ranges SET allocated_numbers = allocated_numbers + ? WHERE name=?",
                     (len(available), body.rangeName))

    return {"message": f"Successfully allocated {len(available)} numbers to {username}"}

@router.post("/allocate")
async def allocate_numbers(request: Request, body: AllocateNumbers, p=Depends(get_current_user)):
    from datetime import timedelta
    now = datetime.utcnow()
    expires_map = {"weekly": 7, "monthly": 30, "yearly": 365}
    days = body.customDays if body.duration == "custom" else expires_map.get(body.duration, 30)
    expires_at = (now + timedelta(days=days)).isoformat()

    with get_db() as conn:
        rng = conn.execute("SELECT * FROM ranges WHERE name=?", (body.rangeName,)).fetchone()
        if not rng: raise HTTPException(404, "Range not found")
        if not rng['status'] == 'active': raise HTTPException(400, "Range is not active")

        per_user_limit = rng['allocation_limit_per_user'] or 100
        global_limit = rng['allocation_limit_global'] or 10000
        allocated = rng['allocated_numbers'] or 0

        if body.quantity > per_user_limit:
            raise HTTPException(400, f"Max {per_user_limit} numbers per request")
        if allocated + body.quantity > global_limit:
            remaining = global_limit - allocated
            if remaining <= 0:
                raise HTTPException(400, "Self-allocation limit reached for this range. Contact support for additional numbers.")
            raise HTTPException(400, f"Only {remaining} allocation slots available. Contact support for more.")

        range_prefix = rng['number_prefix']
        available_rows = conn.execute("""SELECT id,number FROM numbers WHERE range_name=? AND status='active'
                                    AND (assigned_to IS NULL OR assigned_to='')""",
                                  (body.rangeName,)).fetchall()
        if range_prefix:
            available = [r for r in available_rows if r['number'].startswith(range_prefix)]
        else:
            available = available_rows
        available = available[:body.quantity]
        if len(available) < body.quantity:
            raise HTTPException(400, f"Only {len(available)} numbers available in this range")

        now_str = now.isoformat()
        numbers_allocated = []
        for n in available:
            conn.execute("UPDATE numbers SET assigned_to=?,assigned_at=? WHERE id=?",
                         (p['username'], now_str, n['id']))
            numbers_allocated.append(n['number'])

        conn.execute("UPDATE ranges SET allocated_numbers=allocated_numbers+? WHERE id=?",
                     (body.quantity, rng['id']))

        alloc_id = generate_id()
        conn.execute("""INSERT INTO allocations (id,user_id,username,range_name,range_id,quantity,duration,expires_at,status,number_ids)
                        VALUES (?,?,?,?,?,?,?,?,'active',?)""",
                     (alloc_id, p['id'], p['username'], body.rangeName, rng['id'],
                      body.quantity, body.duration, expires_at, ",".join(numbers_allocated)))

    return {"allocated": body.quantity, "expires_at": expires_at, "allocation_id": alloc_id}

@router.get("/allocations")
async def list_allocations(request: Request, status: Optional[str] = None, p=Depends(get_current_user)):
    with get_db() as conn:
        conds, params = [], []
        if p['role'] != 'admin':
            conds.append("user_id = ?")
            params.append(p['id'])
        if status:
            conds.append("status = ?")
            params.append(status)

        where = " AND ".join(conds) if conds else "1=1"
        rows = conn.execute(f"SELECT * FROM allocations WHERE {where} ORDER BY created_at DESC", params).fetchall()
    return {"data": [dict(r) for r in rows]}

@router.post("/allocations/{aid}/return")
async def return_allocation(request: Request, aid: str, p=Depends(get_current_user)):
    with get_db() as conn:
        alloc = conn.execute("SELECT * FROM allocations WHERE id=?", (aid,)).fetchone()
        if not alloc: raise HTTPException(404, "Allocation not found")
        if p['role'] != 'admin' and alloc['user_id'] != p['id']:
            raise HTTPException(403, "Not authorized")
        if alloc['number_ids']:
            nums = [n for n in alloc['number_ids'].split(",") if n]
            for num in nums:
                conn.execute("UPDATE numbers SET assigned_to=NULL,assigned_at=NULL WHERE number=?", (num,))
            conn.execute("UPDATE ranges SET allocated_numbers=MAX(0,allocated_numbers-?) WHERE name=?",
                         (len(nums), alloc['range_name']))
        conn.execute("UPDATE allocations SET status='returned',returned_at=? WHERE id=?",
                     (datetime.utcnow().isoformat(), aid))
    return {"returned": len(nums) if alloc['number_ids'] else 0}

@router.post("/bulk-revoke")
async def bulk_revoke(request: Request, body: BulkRevokeRequest, p=Depends(require_role(["admin", "manager"]))):
    with get_db() as conn:
        count = 0
        if body.scope == "global":
            r = conn.execute("UPDATE numbers SET assigned_to=NULL, assigned_at=NULL WHERE assigned_to IS NOT NULL")
            count = r.rowcount
            conn.execute("UPDATE ranges SET allocated_numbers=0")
        elif body.scope == "user" and body.userId:
            user = conn.execute("SELECT username FROM users WHERE id=?", (body.userId,)).fetchone()
            if not user: raise HTTPException(404, "User not found")
            r = conn.execute("UPDATE numbers SET assigned_to=NULL, assigned_at=NULL WHERE assigned_to=?", (user['username'],))
            count = r.rowcount
            conn.execute("""UPDATE ranges SET allocated_numbers = (
                SELECT COUNT(*) FROM numbers WHERE range_name = ranges.name AND assigned_to IS NOT NULL AND assigned_to != ''
            )""")
        elif body.scope == "range" and body.rangeName:
            r = conn.execute("UPDATE numbers SET assigned_to=NULL, assigned_at=NULL WHERE range_name=?", (body.rangeName,))
            count = r.rowcount
            conn.execute("UPDATE ranges SET allocated_numbers=0 WHERE name=?", (body.rangeName,))
        else:
            raise HTTPException(400, "Invalid scope or missing parameters")

    return {"message": f"Successfully revoked {count} numbers", "revoked_count": count}

@router.get("/blacklist")
async def list_blacklist(request: Request, p=Depends(require_role(["admin", "manager"]))):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM blacklisted_apps").fetchall()
    return {"data": [dict(r) for r in rows]}

@router.post("/blacklist")
async def add_blacklist(request: Request, body: dict, p=Depends(require_role(["admin", "manager"]))):
    app_name = body.get('app_name')
    pattern = body.get('pattern')
    if not app_name or not pattern: raise HTTPException(400, "Missing fields")
    with get_db() as conn:
        bid = generate_id()
        conn.execute("INSERT INTO blacklisted_apps (id, app_name, pattern, created_by) VALUES (?, ?, ?, ?)",
                     (bid, app_name, pattern, p['username']))
    return {"message": "App blacklisted"}

@router.delete("/blacklist/{bid}")
async def delete_blacklist(request: Request, bid: str, p=Depends(require_role(["admin", "manager"]))):
    with get_db() as conn:
        conn.execute("DELETE FROM blacklisted_apps WHERE id=?", (bid,))
    return {"message": "Rule deleted"}

@router.post("/bulk-delete")
async def bulk_delete(request: Request, scope: str, value: str, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        if scope == "range":
            conn.execute("DELETE FROM numbers WHERE range_name=?", (value,))
            conn.execute("UPDATE ranges SET total_numbers=0, allocated_numbers=0 WHERE name=?", (value,))
        elif scope == "status":
            conn.execute("DELETE FROM numbers WHERE status=?", (value,))
            conn.execute("""UPDATE ranges SET total_numbers = (SELECT COUNT(*) FROM numbers WHERE range_id = ranges.id)""")
        else:
            raise HTTPException(400, "Invalid scope")
    return {"message": "Bulk deletion completed"}
