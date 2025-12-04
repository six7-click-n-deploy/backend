from pydantic import BaseModel, EmailStr
from datetime import datetime
from typing import Optional

# ----------------------------------------------------------------
# USER SCHEMAS
# ----------------------------------------------------------------
class UserBase(BaseModel):
    email: EmailStr
    username: str

class UserCreate(UserBase):
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

class UserResponse(UserBase):
    id: int
    created_at: datetime
    
    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

# ----------------------------------------------------------------
# GIT REPOSITORY SCHEMAS
# ----------------------------------------------------------------
class GitRepoBase(BaseModel):
    name: str
    url: str
    branch: str = "main"

class GitRepoCreate(GitRepoBase):
    pass

class GitRepoResponse(GitRepoBase):
    id: int
    user_id: int
    last_commit: Optional[str] = None
    last_cloned_at: Optional[datetime] = None
    created_at: datetime
    
    class Config:
        from_attributes = True

class GitCloneRequest(BaseModel):
    repo_id: int

# ----------------------------------------------------------------
# TASK SCHEMAS
# ----------------------------------------------------------------
class TaskCreate(BaseModel):
    task_type: str
    data: dict = {}

class TaskResponse(BaseModel):
    id: int
    celery_task_id: str
    task_type: str
    status: str
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True