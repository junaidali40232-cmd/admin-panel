"""Dashboard - stats scoped by role"""
from fastapi import APIRouter, Request, HTTPException, Query, Depends
from database import get_db
from auth import verify_token, extract_token
from datetime import datetime, timedelta
from routes.deps import get_current_user, require_role

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

@router.get("/stats")
async def get_stats(request: Request):
    p = get_current_user(request)
    now = datetime.utcnow()
    day_start   = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    week_start  = (now - timedelta(days=now.weekday())).replace(hour=0,minute=0,second=0,microsecond=0).isoformat()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    role = p["role"]
    username = p["username"]
    user_id  = p["id"]

    with get_db() as conn:
        # SMS scope filter
        if role in ["sub_reseller", "test_user"]:
            sms_cond = "assigned_to = ?"
            sms_param = username
        elif role == "reseller":
            my_users = [r["username"] for r in conn.execute(
                "SELECT username FROM users WHERE parent_id=?", (user_id,)
            ).fetchall()]
            names = [username] + my_users
            ph = ",".join("?"*len(names))
            sms_cond = f"assigned_to IN ({ph})"
            sms_param = names
        else:
            sms_cond = "1=1"
            sms_param = None

        def sms_count(extra_where=""):
            q = f"SELECT COUNT(*) FROM sms_received WHERE {sms_cond}"
            if extra_where: q += f" AND {extra_where}"
            if isinstance(sms_param, list): return conn.execute(q, sms_param).fetchone()[0]
            elif sms_param: return conn.execute(q, (sms_param,)).fetchone()[0]
            else: return conn.execute(q).fetchone()[0]

        today_sms = sms_count(f"received_at >= '{day_start}'")
        week_sms  = sms_count(f"received_at >= '{week_start}'")
        month_sms = sms_count(f"received_at >= '{month_start}'")

        # Numbers scope
        if role in ["sub_reseller", "test_user"]:
            num_cond, num_p = "assigned_to=?", (username,)
        elif role == "reseller":
            num_cond = f"assigned_to IN ({ph})"
            num_p = tuple(names)
        else:
            num_cond, num_p = "1=1", ()

        total_numbers  = conn.execute(f"SELECT COUNT(*) FROM numbers WHERE {num_cond}", num_p).fetchone()[0]
        active_numbers = conn.execute(f"SELECT COUNT(*) FROM numbers WHERE {num_cond} AND status='active'", num_p).fetchone()[0]

        # Users count
        if role == "admin":
            total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        elif role == "manager":
            total_users = conn.execute("SELECT COUNT(*) FROM users WHERE role='reseller'").fetchone()[0]
        elif role == "reseller":
            total_users = conn.execute("SELECT COUNT(*) FROM users WHERE parent_id=?", (user_id,)).fetchone()[0]
        else:
            total_users = 0

        # Providers
        active_providers = conn.execute("SELECT COUNT(*) FROM providers WHERE status='active'").fetchone()[0]

        # Allocations
        if role == "admin":
            total_allocations = conn.execute("SELECT COUNT(*) FROM allocations WHERE status='active'").fetchone()[0]
        else:
            total_allocations = conn.execute("SELECT COUNT(*) FROM allocations WHERE user_id=? AND status='active'", (user_id,)).fetchone()[0]

        # DLRs
        total_dlrs = conn.execute("SELECT COUNT(*) FROM sms_received WHERE otp IS NOT NULL").fetchone()[0]

        # Profit
        def profit_sum(extra=""):
            q = "SELECT COALESCE(SUM(profit_amount),0) FROM profit_log"
            if extra: q += f" WHERE {extra}"
            return conn.execute(q).fetchone()[0]

        today_profit = profit_sum(f"created_at >= '{day_start}'")
        month_profit = profit_sum(f"created_at >= '{month_start}'")

        # Chart: sms by day for last 7 days
        week_by_day = []
        for i in range(7):
            ds = (now - timedelta(days=6-i)).replace(hour=0,minute=0,second=0,microsecond=0)
            de = ds + timedelta(days=1)
            q = f"SELECT COUNT(*) FROM sms_received WHERE {sms_cond} AND received_at>=? AND received_at<?"
            args_base = list(sms_param) if isinstance(sms_param, list) else ([sms_param] if sms_param else [])
            cnt = conn.execute(q, args_base + [ds.isoformat(), de.isoformat()]).fetchone()[0]
            week_by_day.append({"date": ds.strftime("%Y-%m-%d"), "count": cnt})

        # Top services today
        q = f"SELECT service, COUNT(*) cnt FROM sms_received WHERE {sms_cond} AND service IS NOT NULL AND received_at>=? GROUP BY service ORDER BY cnt DESC LIMIT 8"
        args_base = list(sms_param) if isinstance(sms_param, list) else ([sms_param] if sms_param else [])
        svc_rows = conn.execute(q, args_base + [day_start]).fetchall()
        services = [{"service": r["service"], "count": r["cnt"]} for r in svc_rows]

    return {
        "todaySms": today_sms,
        "weekSms": week_sms,
        "monthSms": month_sms,
        "todayProfit": today_profit,
        "monthProfit": month_profit,
        "totalNumbers": total_numbers,
        "activeNumbers": active_numbers,
        "totalUsers": total_users,
        "activeProviders": active_providers,
        "totalAllocations": total_allocations,
        "totalDlrs": total_dlrs,
        "todaySmsByService": services,
        "weekSmsByDay": week_by_day,
        "role": role,
    }

@router.get("/recent-sms")
async def recent_sms(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    p = get_current_user(request)
    role, username, user_id = p["role"], p["username"], p["id"]

    with get_db() as conn:
        if role in ["sub_reseller", "test_user"]:
            cond, params = "assigned_to=?", [username]
        elif role == "reseller":
            my = [r["username"] for r in conn.execute("SELECT username FROM users WHERE parent_id=?", (user_id,)).fetchall()]
            names = [username] + my
            cond = f"assigned_to IN ({','.join('?'*len(names))})"
            params = names
        else:
            cond, params = "1=1", []

        rows = conn.execute(
            f"SELECT * FROM sms_received WHERE {cond} ORDER BY received_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        total = conn.execute(f"SELECT COUNT(*) FROM sms_received WHERE {cond}", params).fetchone()[0]

    return {
        "data": [dict(r) for r in rows],
        "pagination": {"total": total, "limit": limit, "offset": offset, "hasMore": offset+limit<total},
    }

@router.get("/analytics")
async def get_analytics(request: Request):
    p = get_current_user(request)
    return {
        "sms_over_time": [],
        "profit_over_time": [],
        "success_rates": {"global": 0.98}
    }

@router.get("/live-activity")
async def get_live_activity(request: Request):
    p = get_current_user(request)
    return {
        "active_users": 5,
        "active_numbers": 120,
        "recent_events": []
    }

@router.get("/audit-logs")
async def dashboard_audit_logs(request: Request, limit: int = Query(50, ge=1, le=100), p=Depends(require_role(["admin", "manager"]))):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return {"data": [dict(r) for r in rows]}

@router.get("/activity-logs")
async def dashboard_activity_logs(request: Request, limit: int = Query(50, ge=1, le=100), p=Depends(get_current_user)):
    # Simulating activity logs from audit logs for now, filtered by user if not admin
    with get_db() as conn:
        if p['role'] == 'admin':
            rows = conn.execute("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM audit_logs WHERE actor_id = ? ORDER BY created_at DESC LIMIT ?", (p['id'], limit)).fetchall()
    return {"data": [dict(r) for r in rows]}
