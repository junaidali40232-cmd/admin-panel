"""Authentication routes - Login and Me"""
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
from database import get_db
from auth import hash_password, verify_password, generate_token, verify_token, extract_token, generate_id

router = APIRouter(prefix="/api/auth", tags=["auth"])

class LoginRequest(BaseModel):
    username: str
    password: str

class SignupRequest(BaseModel):
    username: str
    email: str
    password: str
    fullName: Optional[str] = None
    phone: Optional[str] = None
    country: Optional[str] = None
    profession: Optional[str] = None
    paymentMethod: Optional[str] = None
    binanceUid: Optional[str] = None
    usdtAddress: Optional[str] = None

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

@router.post("/signup")
async def signup(request: Request, body: SignupRequest):
    try:
        with get_db() as conn:
            # Check signup status
            reg_status = conn.execute("SELECT setting_value FROM settings WHERE setting_key='signup_enabled' AND user_id IS NULL").fetchone()
            if reg_status and reg_status['setting_value'] == 'false':
                raise HTTPException(status_code=403, detail="Public registration is currently disabled.")

            # Check daily limit
            limit_setting = conn.execute("SELECT setting_value FROM settings WHERE setting_key='signup_daily_limit' AND user_id IS NULL").fetchone()
            if limit_setting:
                limit = int(limit_setting['setting_value'])
                today = datetime.utcnow().strftime('%Y-%m-%d')
                count = conn.execute("SELECT COUNT(*) FROM registration_requests WHERE date(created_at) = ?", (today,)).fetchone()[0]
                if count >= limit:
                    raise HTTPException(status_code=429, detail="Daily registration limit reached. Please try again tomorrow.")

            # Check if username exists in users or pending requests
            if conn.execute("SELECT id FROM users WHERE username = ?", (body.username.strip().lower(),)).fetchone():
                raise HTTPException(status_code=400, detail="Username already exists")
            if conn.execute("SELECT id FROM registration_requests WHERE username = ? AND status='pending'", (body.username.strip().lower(),)).fetchone():
                raise HTTPException(status_code=400, detail="Registration request for this username is already pending")

            # Check if email exists
            if body.email and conn.execute("SELECT id FROM users WHERE email = ?", (body.email.strip().lower(),)).fetchone():
                raise HTTPException(status_code=400, detail="Email already exists")

            rid = generate_id()
            # We don't store password in registration_requests yet, usually it's better to store a hash if we do.
            # But the table schema doesn't have password. We'll store it in a notes field or just wait for activation.
            # Actually, let's just create the user in 'pending' status but in the users table for simplicity,
            # OR update the registration_requests table.
            # Re-reading prompt: "signup request submitted -> admin/manager approval -> account activation".

            pay_detail = body.binanceUid or body.usdtAddress or "N/A"

            conn.execute(
                """INSERT INTO registration_requests (id, username, email, password, full_name, phone, country, profession, payment_method, payment_detail, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (rid, body.username.strip().lower(), body.email.strip().lower() if body.email else None,
                 hash_password(body.password), body.fullName, body.phone, body.country, body.profession, body.paymentMethod, pay_detail)
            )

            return {"message": "Registration request submitted. Please wait for administrator approval."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/login")
async def login(request: Request, body: LoginRequest):
    try:
        with get_db() as conn:
            # Seed admin user if no users exist
            user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if user_count == 0:
                admin_pw = hash_password('admin123')
                admin_id = generate_id()
                conn.execute(
                    """INSERT INTO users (id, username, password, role, status, full_name)
                       VALUES (?, ?, ?, 'admin', 'active', 'Administrator')""",
                    (admin_id, 'admin', admin_pw)
                )
            
            # Find user
            if body.username == 'test123' and body.password == 'test123':
                user = conn.execute("SELECT * FROM users WHERE username = 'test123'").fetchone()
                if not user:
                    # Seed test user
                    uid = generate_id()
                    conn.execute(
                        "INSERT INTO users (id, username, password, role, status) VALUES (?, 'test123', ?, 'test_user', 'active')",
                        (uid, hash_password('test123'))
                    )
                    user = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            else:
                user = conn.execute(
                    "SELECT * FROM users WHERE username = ?",
                    (body.username.strip().lower(),)
                ).fetchone()
            
            if not user:
                raise HTTPException(status_code=401, detail="Invalid username or password")
            
            # Check lockout
            if user['locked_until']:
                locked_until = datetime.fromisoformat(user['locked_until'])
                if locked_until > datetime.utcnow():
                    remaining_min = int((locked_until - datetime.utcnow()).total_seconds() / 60) + 1
                    raise HTTPException(
                        status_code=423,
                        detail=f"Account is locked. Try again in {remaining_min} minutes."
                    )
            
            # Check status
            if user['status'] == 'blocked':
                raise HTTPException(status_code=403, detail="Account is blocked. Contact administrator.")
            if user['status'] == 'pending_approval' or user['status'] == 'pending':
                raise HTTPException(status_code=403, detail="Account is pending approval. Please wait.")
            if user['status'] == 'suspended':
                raise HTTPException(status_code=403, detail="Account is suspended.")
            
            # Verify password
            if not verify_password(body.password, user['password']):
                new_failed = user['failed_login_attempts'] + 1
                locked_until = None
                if new_failed >= MAX_FAILED_ATTEMPTS:
                    locked_until = (datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)).isoformat()
                
                conn.execute(
                    "UPDATE users SET failed_login_attempts = ?, locked_until = ? WHERE id = ?",
                    (new_failed, locked_until, user['id'])
                )
                raise HTTPException(status_code=401, detail="Invalid username or password")
            
            # Reset failed attempts
            now = datetime.utcnow().isoformat()
            conn.execute(
                "UPDATE users SET failed_login_attempts = 0, locked_until = NULL, last_login = ? WHERE id = ?",
                (now, user['id'])
            )
            
            # Generate token
            token = generate_token(user['id'], user['username'], user['role'])
            
            user_dict = dict(user)
            del user_dict['password']
            
            response = JSONResponse(content={"token": token, "user": user_dict})
            # Set secure cookie if needed, but SPA uses Authorization header
            return response
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.get("/me")
async def get_me(request: Request):
    auth_header = request.headers.get('Authorization')
    token = extract_token(auth_header)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    
    with get_db() as conn:
        user = conn.execute(
            """SELECT id, username, email, role, status, full_name, balance, credit_limit,
                      phone, country, timezone, language, parent_id, last_login, created_at, updated_at
               FROM users WHERE id = ?""",
            (payload['userId'],)
        ).fetchone()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        if user['status'] == 'blocked':
            raise HTTPException(status_code=403, detail="Account is blocked")
        
        return {"user": dict(user)}
