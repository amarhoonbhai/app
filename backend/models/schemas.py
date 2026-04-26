from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class AccountBase(BaseModel):
    phone: str
    name: str
    api_id: int
    api_hash: str
    cycle_delay_min: int = 20
    msg_delay_sec: int = 300
    use_copy: bool = True

class AccountCreate(AccountBase):
    pass

class AccountUpdate(BaseModel):
    name: Optional[str] = None
    cycle_delay_min: Optional[int] = None
    msg_delay_sec: Optional[int] = None
    use_copy: Optional[bool] = None

class AccountResponse(AccountBase):
    id: int
    created_at: datetime
    last_active: Optional[datetime] = None

    class Config:
        from_attributes = True

class GroupBase(BaseModel):
    url: str

class GroupCreate(GroupBase):
    account_id: int

class GroupResponse(GroupBase):
    id: int
    account_id: int

    class Config:
        from_attributes = True

class StatsResponse(BaseModel):
    account_id: int
    success_total: int
    fail_total: int
    current_cycle_success: int
    current_cycle_fail: int
    status: str
    next_msg_at: Optional[datetime] = None
    last_cycle_at: Optional[datetime] = None
