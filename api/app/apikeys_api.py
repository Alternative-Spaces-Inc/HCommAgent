from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .db import get_db
from .apikeys import issue_api_key

router = APIRouter(prefix="/v1/api-keys", tags=["api-keys"])


class ApiKeyIssueRequest(BaseModel):
    caller: str


class ApiKeyIssueResponse(BaseModel):
    api_key: str
    prefix: str
    caller: str
    created_at: datetime


@router.post("", response_model=ApiKeyIssueResponse, status_code=201)
def issue_key(body: ApiKeyIssueRequest, db: Session = Depends(get_db)):
    result = issue_api_key(db, body.caller)
    return result
