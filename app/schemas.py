from pydantic import BaseModel, EmailStr, Field, ConfigDict
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID
from app.models import UserRole, TaskType, TaskStatus

# ----------------------------------------------------------------
# USER SCHEMAS
# ----------------------------------------------------------------
class UserBase(BaseModel):
    email: EmailStr
    username: str

class UserCreate(UserBase):
    role: UserRole = UserRole.STUDENT
    courseId: Optional[UUID] = None

class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    username: Optional[str] = None
    role: Optional[UserRole] = None
    courseId: Optional[UUID] = None


class UserResponse(UserBase):
    userId: UUID
    role: UserRole
    courseId: Optional[UUID] = None
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class UserWithCourse(UserResponse):
    course: Optional['CourseResponse'] = None
    
    model_config = ConfigDict(from_attributes=True)

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

# ----------------------------------------------------------------
# COURSE SCHEMAS
# ----------------------------------------------------------------
class CourseBase(BaseModel):
    name: str

class CourseCreate(CourseBase):
    pass

class CourseUpdate(BaseModel):
    name: Optional[str] = None

class CourseResponse(CourseBase):
    courseId: UUID
    
    model_config = ConfigDict(from_attributes=True)

class CourseWithUsers(CourseResponse):
    users: List[UserResponse] = []
    
    model_config = ConfigDict(from_attributes=True)

# ----------------------------------------------------------------
# APP SCHEMAS
# ----------------------------------------------------------------
class AppBase(BaseModel):
    name: str
    description: Optional[str] = None
    git_link: Optional[str] = None

class AppCreate(AppBase):
    pass

class AppUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    git_link: Optional[str] = None
    image: Optional[bytes] = None

class AppResponse(AppBase):
    appId: UUID
    userId: UUID
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class AppWithUser(AppResponse):
    user: UserResponse
    
    model_config = ConfigDict(from_attributes=True)

class AppWithVersions(AppWithUser):
    versions: List[Dict[str, str]] = []
    
    model_config = ConfigDict(from_attributes=True)

# ----------------------------------------------------------------
# DEPLOYMENT SCHEMAS
# ----------------------------------------------------------------
class Team(BaseModel):
    name: str
    userIds: List[UUID] = []

class DeploymentBase(BaseModel):
    name: str
    appId: UUID

class DeploymentCreate(DeploymentBase):
    releaseTag: Optional[str] = None
    userInputVar: Optional[str] = None
    teams: List[Team] = []

class DeploymentResponse(DeploymentBase):
    deploymentId: UUID
    userId: UUID
    releaseTag: Optional[str] = None
    userInputVar: Optional[str] = None
    status: Optional[str] = None  # From latest task
    created_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)

class DeploymentWithRelations(DeploymentResponse):
    user: UserResponse
    app: AppResponse
    
    model_config = ConfigDict(from_attributes=True)


# Team response for deployment details (from DB)
class DeploymentTeamMember(BaseModel):
    userId: UUID
    email: str
    username: str
    
    model_config = ConfigDict(from_attributes=True)

class DeploymentTeamResponse(BaseModel):
    teamId: UUID
    name: str
    members: List[DeploymentTeamMember] = []
    
    model_config = ConfigDict(from_attributes=True)


# Task summary for deployment details
class TaskSummary(BaseModel):
    taskId: UUID
    type: TaskType
    status: TaskStatus
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# Terraform outputs parsed
class DeploymentOutputs(BaseModel):
    """Parsed Terraform outputs - structure depends on app"""
    raw: Optional[Dict[str, Any]] = None  # Full outputs as dict
    
    model_config = ConfigDict(from_attributes=True)


# Full deployment detail response
class DeploymentDetail(DeploymentWithRelations):
    """Full deployment details with teams, task info, and outputs"""
    teams: List[DeploymentTeamResponse] = []
    latest_task: Optional[TaskSummary] = None
    outputs: Optional[DeploymentOutputs] = None
    logs: Optional[str] = None  # Optional: can be excluded for large logs
    
    model_config = ConfigDict(from_attributes=True)

# ----------------------------------------------------------------
# Task SCHEMAS
# ----------------------------------------------------------------
class TaskBase(BaseModel):
    deploymentId: UUID
    celeryTaskId: str
    type: TaskType
    status: TaskStatus
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    logs: Optional[str] = None
    tf_state: Optional[str] = None
    outputs: Optional[str] = None

class TaskCreate(TaskBase):
    pass

class TaskUpdate(BaseModel):
    status: Optional[TaskStatus] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    logs: Optional[str] = None
    tf_state: Optional[str] = None
    outputs: Optional[str] = None

class TaskResponse(TaskBase):
    taskId: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# ----------------------------------------------------------------
# USER GROUP SCHEMAS
# ----------------------------------------------------------------
class UserGroupBase(BaseModel):
    deploymentId: UUID

class UserGroupCreate(UserGroupBase):
    userIds: List[UUID] = []
    courseIds: List[UUID] = []

class UserGroupResponse(UserGroupBase):
    userGroupId: UUID
    
    model_config = ConfigDict(from_attributes=True)

class UserGroupWithMembers(UserGroupResponse):
    users: List[UserResponse] = []
    courses: List[CourseResponse] = []
    
    model_config = ConfigDict(from_attributes=True)

# ----------------------------------------------------------------
# TEAM SCHEMAS
# ----------------------------------------------------------------
class TeamBase(BaseModel):
    name: str
    userGroupId: UUID

class TeamCreate(TeamBase):
    userIds: List[UUID] = []

class TeamUpdate(BaseModel):
    name: Optional[str] = None

class TeamResponse(TeamBase):
    teamId: UUID
    
    model_config = ConfigDict(from_attributes=True)

class TeamWithMembers(TeamResponse):
    users: List[UserResponse] = []
    
    model_config = ConfigDict(from_attributes=True)

# ----------------------------------------------------------------
# STATISTICS SCHEMAS
# ----------------------------------------------------------------
class UserStatistics(BaseModel):
    total_apps: int
    total_deployments: int
    successful_deployments: int
    failed_deployments: int
    pending_deployments: int

class CourseStatistics(BaseModel):
    total_students: int
    total_teachers: int
    total_apps: int
    total_deployments: int