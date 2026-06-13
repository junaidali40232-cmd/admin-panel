"""Numbers routes - role-based: admin/manager full control, reseller assigns to users, user read-only"""
from fastapi import APIRouter, Request, HTTPException, Query, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import re
from database import get_db
from auth import verify_token, extract_token, generate_id
from datetime import datetime
from routes.deps import get_current_user, require_role

router = APIRouter(prefix="/api/numbers", tags=["numbers"])

class NumberCreate(BaseModel):
    number: str
    country: Optional[str] = None
    countryName: Optional[str] = None
    rangeName: Optional[str] = None
    rangeId: Optional[str] = None
    service: Optional[str] = None
    status: Optional[str] = "active"
    assignedTo: Optional[str] = None
    rate: Optional[float] = 0
    profitMargin: Optional[float] = 0

class NumberUpdate(BaseModel):
    country: Optional[str] = None
    countryName: Optional[str] = None
    rangeName: Optional[str] = None
    rangeId: Optional[str] = None
    service: Optional[str] = None
    status: Optional[str] = None
    assignedTo: Optional[str] = None
    rate: Optional[float] = None
    profitMargin: Optional[float] = None

class BulkImport(BaseModel):
    numbersText: str
    country: str
    countryName: Optional[str] = None
    rangeName: Optional[str] = None
    rangeId: Optional[str] = None
    rate: Optional[float] = 0.0
    profitMargin: Optional[float] = 50.0

class AssignBody(BaseModel):
    numberIds: list
    assignTo: str

class ReturnBody(BaseModel):
    username: Optional[str] = None
    rangeName: Optional[str] = None
    numberIds: Optional[list] = None

@router.get("")
async def list_numbers(
    request: Request,
    country: str = Query(None),
    service: str = Query(None),
    status: str = Query(None),
    search: str = Query(None),
    rangeName: str = Query(None),
    assignedTo: str = Query(None),
    available: str = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    p=Depends(get_current_user)
):
    offset = (page - 1) * limit
    conds, params = [], []
    role = p["role"]

    if role == "sub_reseller":
        conds.append("n.assigned_to = ?")
        params.append(p["username"])
    elif role == "reseller":
        conds.append("(n.assigned_to = ? OR n.assigned_to IN (SELECT username FROM users WHERE parent_id = ?))")
        params.extend([p["username"], p["id"]])

    if country:
        conds.append("n.country = ?"); params.append(country)
    if service:
        conds.append("n.service LIKE ?"); params.append(f"%{service}%")
    if status:
        conds.append("n.status = ?"); params.append(status)
    if rangeName:
        conds.append("n.range_name = ?"); params.append(rangeName)
    if assignedTo:
        conds.append("n.assigned_to = ?"); params.append(assignedTo)
    if search:
        conds.append("(n.number LIKE ? OR n.country_name LIKE ? OR n.service LIKE ? OR n.range_name LIKE ?)")
        params.extend([f"%{search}%"] * 4)
    if available == "true":
        conds.append("(n.assigned_to IS NULL OR n.assigned_to = '')")

    where = " AND ".join(conds) if conds else "1=1"

    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT n.* FROM numbers n WHERE {where}
                ORDER BY n.created_at DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
        total = conn.execute(f"SELECT COUNT(*) FROM numbers n WHERE {where}", params).fetchone()[0]

    return {
        "data": [dict(r) for r in rows],
        "pagination": {
            "total": total, "page": page, "limit": limit,
            "totalPages": (total + limit - 1) // limit,
            "hasMore": offset + limit < total,
        },
    }

@router.post("")
async def create_number(request: Request, body: NumberCreate, p=Depends(require_role(["admin", "manager"]))):
    with get_db() as conn:
        if conn.execute("SELECT id FROM numbers WHERE number=?", (body.number,)).fetchone():
            raise HTTPException(409, "Number already exists")
        if body.rangeId:
            rng = conn.execute("SELECT number_prefix FROM ranges WHERE id=?", (body.rangeId,)).fetchone()
            if rng and rng['number_prefix']:
                if not body.number.startswith(rng['number_prefix']):
                    raise HTTPException(400, f"Number must start with prefix '{rng['number_prefix']}' for this range")
        nid = generate_id()
        conn.execute(
            """INSERT INTO numbers (id,number,country,country_name,range_name,range_id,
               service,status,assigned_to,rate,profit_margin)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (nid, body.number, body.country, body.countryName, body.rangeName,
             body.rangeId, body.service, body.status, body.assignedTo, body.rate, body.profitMargin),
        )
        row = conn.execute("SELECT * FROM numbers WHERE id=?", (nid,)).fetchone()
    return JSONResponse(status_code=201, content={"data": dict(row), "number": body.number})

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
                conn.execute(
                    """INSERT INTO numbers (id,number,country,country_name,range_name,range_id,rate,profit_margin,status)
                       VALUES (?,?,?,?,?,?,?,?,'active')""",
                    (generate_id(), num, body.country, body.countryName,
                     body.rangeName, body.rangeId, body.rate, body.profitMargin),
                )
                success += 1
                added_numbers.append(num)
            except Exception as e:
                errors.append(f"{num}: {e}")
    return {"success": success, "skipped": skipped, "errors": errors[:20], "total": len(lines), "added_numbers": added_numbers}

@router.post("/assign")
async def assign_numbers(request: Request, body: AssignBody, p=Depends(require_role(["admin", "manager", "reseller"]))):
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        if p["role"] == "reseller":
            target_user = conn.execute(
                "SELECT * FROM users WHERE username=? AND parent_id=?",
                (body.assignTo, p["id"])
            ).fetchone()
            if not target_user:
                raise HTTPException(403, "You can only assign numbers to your own users")
        count = 0
        for nid in body.numberIds:
            conn.execute("UPDATE numbers SET assigned_to=?,assigned_at=? WHERE id=?",
                         (body.assignTo, now, nid))
            count += 1
    return {"assigned": count, "to": body.assignTo}

@router.post("/return")
async def return_numbers(request: Request, body: ReturnBody, p=Depends(require_role(["admin", "manager"]))):
    with get_db() as conn:
        if body.numberIds:
            placeholders = ",".join("?" * len(body.numberIds))
            r = conn.execute(
                f"UPDATE numbers SET assigned_to=NULL,assigned_at=NULL WHERE id IN ({placeholders})",
                body.numberIds
            )
            count = r.rowcount
        elif body.username and body.rangeName:
            r = conn.execute(
                "UPDATE numbers SET assigned_to=NULL,assigned_at=NULL WHERE assigned_to=? AND range_name=?",
                (body.username, body.rangeName)
            )
            count = r.rowcount
        elif body.username:
            r = conn.execute(
                "UPDATE numbers SET assigned_to=NULL,assigned_at=NULL WHERE assigned_to=?",
                (body.username,)
            )
            count = r.rowcount
        elif body.rangeName:
            r = conn.execute(
                "UPDATE numbers SET assigned_to=NULL,assigned_at=NULL WHERE range_name=?",
                (body.rangeName,)
            )
            count = r.rowcount
        else:
            raise HTTPException(400, "Specify username, rangeName, or numberIds")
    return {"returned": count}

@router.put("/{item_id}")
async def update_number(request: Request, item_id: str, body: NumberUpdate, p=Depends(require_role(["admin", "manager"]))):
    with get_db() as conn:
        if not conn.execute("SELECT id FROM numbers WHERE id=?", (item_id,)).fetchone():
            raise HTTPException(404, "Number not found")
        updates = {}
        fm = {"country":"country","countryName":"country_name","rangeName":"range_name",
              "rangeId":"range_id","service":"service","status":"status",
              "assignedTo":"assigned_to","rate":"rate","profitMargin":"profit_margin"}
        for k, db in fm.items():
            v = getattr(body, k, None)
            if v is not None: updates[db] = v
        if updates:
            conn.execute(
                f"UPDATE numbers SET {','.join(f'{k}=?' for k in updates)} WHERE id=?",
                list(updates.values()) + [item_id]
            )
        row = conn.execute("SELECT * FROM numbers WHERE id=?", (item_id,)).fetchone()
    return {"data": dict(row)}

@router.delete("/{item_id}")
async def delete_number(request: Request, item_id: str, p=Depends(require_role(["admin", "manager"]))):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM numbers WHERE id=?", (item_id,)).fetchone()
        if not row: raise HTTPException(404, "Number not found")
        conn.execute("DELETE FROM numbers WHERE id=?", (item_id,))
    return {"message": "Deleted", "number": row["number"]}

@router.get("/test-panel")
async def list_test_numbers(
    request: Request,
    rangeId: str = Query(None),
    p=Depends(get_current_user)
):
    """Provides rotating random test numbers for the Test Panel."""
    with get_db() as conn:
        # Get all active ranges
        ranges_query = "SELECT * FROM ranges WHERE status = 'active'"
        params = []
        if rangeId:
            ranges_query += " AND id = ?"
            params.append(rangeId)

        ranges = conn.execute(ranges_query, params).fetchall()

        result = []
        for r in ranges:
            # Get 10 random active numbers for this range
            numbers = conn.execute(
                """SELECT n.* FROM numbers n
                   WHERE n.range_id = ? AND n.status = 'active'
                   ORDER BY RANDOM() LIMIT 10""",
                (r["id"],)
            ).fetchall()

            for n in numbers:
                row = dict(n)
                row["range_name"] = r["name"]
                row["number_prefix"] = r["number_prefix"]
                result.append(row)

    return {"data": result}
