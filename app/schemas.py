from pydantic import BaseModel, EmailStr
from datetime import datetime
from typing import Optional
from uuid import UUID

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
    userId: UUID
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

# ----------------------------------------------------------------
# APP SCHEMAS
# ----------------------------------------------------------------
class AppBase(BaseModel):
    name: str
    description: Optional[str] = None
    git_link: Optional[str] = None

class AppCreate(AppBase):
    """Schema for creating a new app"""
    image: Optional[bytes] = None  # Base64 decoded bytes

class AppUpdate(BaseModel):
    """Schema for updating an app - all fields optional"""
    name: Optional[str] = None
    description: Optional[str] = None
    git_link: Optional[str] = None
    image: Optional[bytes] = None

class AppResponse(AppBase):
    """Schema for app response"""
    appId: UUID
    userId: UUID
    created_at: datetime
    # Note: image excluded from list responses (too large)
    
    class Config:
        from_attributes = True

class AppDetailResponse(AppResponse):
    """Schema for detailed app response including image"""
    image: Optional[bytes] = None
    
    class Config:
        from_attributes = True