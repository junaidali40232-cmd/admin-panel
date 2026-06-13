from fastapi import Request, HTTPException, Depends
from auth import extract_token, verify_token
from database import get_db

def get_current_user(request: Request):
    auth_header = request.headers.get('Authorization')
    token = extract_token(auth_header)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")

    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (payload['userId'],)
        ).fetchone()

        if not user:
            raise HTTPException(status_code=401, detail="User not found")

        if user['status'] == 'blocked' or user['status'] == 'suspended':
            raise HTTPException(status_code=403, detail="Account is restricted")

        return user

def require_role(roles: list):
    def role_checker(user=Depends(get_current_user)):
        if user['role'] not in roles:
            raise HTTPException(status_code=403, detail="Permission denied")
        return user
    return role_checker
