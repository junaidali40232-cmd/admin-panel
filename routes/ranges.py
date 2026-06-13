"""Ranges CRUD - Admin only can create/delete, all roles can view"""
from fastapi import APIRouter, Request, HTTPException, Query, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
from database import get_db
from auth import verify_token, extract_token, generate_id
from routes.deps import get_current_user, require_role

router = APIRouter(prefix="/api/ranges", tags=["ranges"])

class RangeCreate(BaseModel):
    name: str
    numberPrefix: str
    providerId: Optional[str] = None
    countryCode: Optional[str] = None
    countryName: Optional[str] = None
    rate: Optional[float] = 0
    profitMargin: Optional[float] = 0
    otpLimitPerDay: Optional[int] = 0
    otpDailyResetHour: Optional[int] = 0
    allocationLimitGlobal: Optional[int] = 10000
    allocationLimitPerUser: Optional[int] = 100
    allocationPeriod: Optional[str] = "monthly"
    status: Optional[str] = "active"
    testNumbers: Optional[str] = None

class RangeUpdate(BaseModel):
    name: Optional[str] = None
    numberPrefix: Optional[str] = None
    providerId: Optional[str] = None
    countryCode: Optional[str] = None
    countryName: Optional[str] = None
    rate: Optional[float] = None
    profitMargin: Optional[float] = None
    otpLimitPerDay: Optional[int] = None
    otpDailyResetHour: Optional[int] = None
    allocationLimitGlobal: Optional[int] = None
    allocationLimitPerUser: Optional[int] = None
    allocationPeriod: Optional[str] = None
    status: Optional[str] = None
    testNumbers: Optional[str] = None

FIELD_MAP = {
    "name": "name", "numberPrefix": "number_prefix", "providerId": "provider_id", "countryCode": "country_code",
    "countryName": "country_name", "rate": "rate", "profitMargin": "profit_margin",
    "otpLimitPerDay": "otp_limit_per_day", "otpDailyResetHour": "otp_daily_reset_hour",
    "allocationLimitGlobal": "allocation_limit_global",
    "allocationLimitPerUser": "allocation_limit_per_user",
    "allocationPeriod": "allocation_period", "status": "status",
    "testNumbers": "test_numbers"
}

@router.get("")
async def list_ranges(
    request: Request,
    country: str = Query(None), status: str = Query(None),
    search: str = Query(None),
    page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
    p=Depends(get_current_user)
):
    offset = (page - 1) * limit
    conds, params = [], []
    if country: conds.append("country_code = ?"); params.append(country)
    if status:  conds.append("status = ?");      params.append(status)
    if search:
        conds.append("(name LIKE ? OR country_name LIKE ?)"); params.extend([f"%{search}%"]*2)
    where = " AND ".join(conds) if conds else "1=1"
    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT r.*,
                (SELECT COUNT(*) FROM numbers WHERE range_name = r.name) as numbers_count,
                (SELECT COUNT(*) FROM numbers WHERE range_name = r.name AND (assigned_to IS NULL OR assigned_to='')) as available_count
                FROM ranges r WHERE {where} ORDER BY r.created_at DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
        total = conn.execute(f"SELECT COUNT(*) FROM ranges WHERE {where}", params).fetchone()[0]
    data = []
    for row in rows:
        d = dict(row)
        d["_count"] = {"numbers": d.pop("numbers_count", 0), "available": d.pop("available_count", 0)}
        data.append(d)
    return {"data": data, "pagination": {"total": total, "page": page, "limit": limit, "totalPages": (total+limit-1)//limit, "hasMore": offset+limit<total}}

@router.post("")
async def create_range(request: Request, body: RangeCreate, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        if conn.execute("SELECT id FROM ranges WHERE name=?", (body.name,)).fetchone():
            raise HTTPException(409, "Range name already exists")
        rid = generate_id()
        conn.execute(
            """INSERT INTO ranges (id,name,number_prefix,provider_id,country_code,country_name,rate,profit_margin,
               otp_limit_per_day,otp_daily_reset_hour,allocation_limit_global,allocation_limit_per_user,
               allocation_period,status,test_numbers) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, body.name, body.numberPrefix, body.providerId, body.countryCode, body.countryName,
             body.rate, body.profitMargin, body.otpLimitPerDay, body.otpDailyResetHour,
             body.allocationLimitGlobal, body.allocationLimitPerUser, body.allocationPeriod, body.status,
             body.testNumbers),
        )
        row = conn.execute("SELECT * FROM ranges WHERE id=?", (rid,)).fetchone()
    return JSONResponse(status_code=201, content={"data": dict(row)})

@router.get("/{item_id}")
async def get_range(request: Request, item_id: str, p=Depends(get_current_user)):
    with get_db() as conn:
        row = conn.execute(
            """SELECT r.*,
               (SELECT COUNT(*) FROM numbers WHERE range_name=r.name) as numbers_count,
               (SELECT COUNT(*) FROM numbers WHERE range_name=r.name AND (assigned_to IS NULL OR assigned_to='')) as available_count
               FROM ranges r WHERE r.id=?""", (item_id,)
        ).fetchone()
    if not row: raise HTTPException(404, "Range not found")
    d = dict(row)
    d["_count"] = {"numbers": d.pop("numbers_count", 0), "available": d.pop("available_count", 0)}
    return {"data": d}

@router.put("/{item_id}")
async def update_range(request: Request, item_id: str, body: RangeUpdate, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        if not conn.execute("SELECT id FROM ranges WHERE id=?", (item_id,)).fetchone():
            raise HTTPException(404, "Range not found")
        updates = {db: getattr(body, py) for py, db in FIELD_MAP.items() if getattr(body, py, None) is not None}
        if updates:
            conn.execute(
                f"UPDATE ranges SET {','.join(f'{k}=?' for k in updates)},updated_at=datetime('now') WHERE id=?",
                list(updates.values()) + [item_id],
            )
        row = conn.execute("SELECT * FROM ranges WHERE id=?", (item_id,)).fetchone()
    return {"data": dict(row)}

@router.delete("/{item_id}")
async def delete_range(request: Request, item_id: str, p=Depends(require_role(["admin"]))):
    with get_db() as conn:
        existing = conn.execute("SELECT * FROM ranges WHERE id=?", (item_id,)).fetchone()
        if not existing: raise HTTPException(404, "Range not found")
        conn.execute("UPDATE numbers SET range_id=NULL,range_name=NULL WHERE range_id=?", (item_id,))
        conn.execute("DELETE FROM ranges WHERE id=?", (item_id,))
    return {"message": "Range deleted", "deletedRange": existing["name"]}
