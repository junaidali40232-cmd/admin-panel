"""Provider management routes"""
from fastapi import APIRouter, Request, HTTPException, Query, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
from database import get_db
from auth import verify_token, extract_token, generate_id
from routes.deps import get_current_user, require_role

router = APIRouter(prefix="/api/providers", tags=["providers"])

class ProviderCreate(BaseModel):
    name: str
    type: Optional[str] = 'http'   # http | smpp
    status: Optional[str] = 'active'
    # HTTP
    apiUrl: Optional[str] = None
    apiToken: Optional[str] = None
    apiMethod: Optional[str] = 'POST'
    fieldTo: Optional[str] = 'to'
    fieldFrom: Optional[str] = 'from'
    fieldMsg: Optional[str] = 'msg'
    fieldUuid: Optional[str] = 'uuid'
    # SMPP
    smppHost: Optional[str] = None
    smppPort: Optional[int] = 2775
    smppSystemId: Optional[str] = None
    smppPassword: Optional[str] = None
    smppSystemType: Optional[str] = ''
    smppServiceType: Optional[str] = None
    smppSourceTon: Optional[int] = 1
    smppSourceNpi: Optional[int] = 1
    smppDestTon: Optional[int] = 1
    smppDestNpi: Optional[int] = 1
    smppDataCoding: Optional[int] = 0  # 0=GSM7, 8=UCS2
    notes: Optional[str] = None

class ProviderUpdate(ProviderCreate):
    name: Optional[str] = None

@router.get("")
async def list_providers(request: Request, p=Depends(get_current_user)):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM providers ORDER BY created_at DESC").fetchall()
    return {"data": [dict(r) for r in rows]}

@router.post("")
async def create_provider(request: Request, body: ProviderCreate, p=Depends(require_role(["admin", "manager"]))):
    with get_db() as conn:
        if conn.execute("SELECT id FROM providers WHERE name=?", (body.name,)).fetchone():
            raise HTTPException(409, "Provider name already exists")
        pid = generate_id()
        conn.execute("""INSERT INTO providers
            (id,name,type,status,api_url,api_token,api_method,field_to,field_from,field_msg,field_uuid,
             smpp_host,smpp_port,smpp_system_id,smpp_password,smpp_system_type,smpp_service_type,
             smpp_source_ton,smpp_source_npi,smpp_dest_ton,smpp_dest_npi,smpp_data_coding,notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pid,body.name,body.type,body.status,body.apiUrl,body.apiToken,body.apiMethod,
             body.fieldTo,body.fieldFrom,body.fieldMsg,body.fieldUuid,
             body.smppHost,body.smppPort,body.smppSystemId,body.smppPassword,
             body.smppSystemType,body.smppServiceType,body.smppSourceTon,body.smppSourceNpi,
             body.smppDestTon,body.smppDestNpi,body.smppDataCoding,body.notes))
        row = conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
    return JSONResponse(status_code=201, content={"data": dict(row)})

@router.put("/{pid}")
async def update_provider(request: Request, pid: str, body: ProviderUpdate, p=Depends(require_role(["admin", "manager"]))):
    with get_db() as conn:
        if not conn.execute("SELECT id FROM providers WHERE id=?", (pid,)).fetchone():
            raise HTTPException(404, "Provider not found")
        fmap = {
            'name':'name','type':'type','status':'status','apiUrl':'api_url','apiToken':'api_token',
            'apiMethod':'api_method','fieldTo':'field_to','fieldFrom':'field_from','fieldMsg':'field_msg',
            'fieldUuid':'field_uuid','smppHost':'smpp_host','smppPort':'smpp_port',
            'smppSystemId':'smpp_system_id','smppPassword':'smpp_password','smppSystemType':'smpp_system_type',
            'smppServiceType':'smpp_service_type','smppSourceTon':'smpp_source_ton','smppSourceNpi':'smpp_source_npi',
            'smppDestTon':'smpp_dest_ton','smppDestNpi':'smpp_dest_npi','smppDataCoding':'smpp_data_coding','notes':'notes'
        }
        upd = {v: getattr(body, k) for k,v in fmap.items() if getattr(body, k, None) is not None}
        if upd:
            conn.execute(f"UPDATE providers SET {','.join(f'{k}=?' for k in upd)} WHERE id=?",
                         list(upd.values())+[pid])
        row = conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
    return {"data": dict(row)}

@router.get("/logs")
async def provider_logs(request: Request, p=Depends(require_role(["admin", "manager"]))):
    return {"data": []}

@router.get("/throughput")
async def provider_throughput(request: Request, p=Depends(require_role(["admin", "manager"]))):
    return {"data": {}}

@router.delete("/{pid}")
async def delete_provider(request: Request, pid: str, p=Depends(require_role(["admin", "manager"]))):
    with get_db() as conn:
        r = conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
        if not r: raise HTTPException(404, "Provider not found")
        conn.execute("DELETE FROM providers WHERE id=?", (pid,))
    return {"message": "Provider deleted", "name": r['name']}
