from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models import TaskStatus, TaskType, UserRole


# ----------------------------------------------------------------
# USER SCHEMAS
# ----------------------------------------------------------------
class UserBase(BaseModel):
    email: EmailStr
    username: str

class UserCreate(UserBase):
    role: UserRole = UserRole.STUDENT
    courseId: UUID | None = None

class UserUpdate(BaseModel):
    email: EmailStr | None = None
    username: str | None = None
    role: UserRole | None = None
    courseId: UUID | None = None


class UserResponse(UserBase):
    userId: UUID
    role: UserRole
    courseId: UUID | None = None
    keycloak_id: str | None = None
    firstName: str | None = None
    lastName: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class UserWithCourse(UserResponse):
    course: Optional['CourseResponse'] = None

    model_config = ConfigDict(from_attributes=True)

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: str | None = None

# ----------------------------------------------------------------
# COURSE SCHEMAS
# ----------------------------------------------------------------
class CourseBase(BaseModel):
    name: str

class CourseCreate(CourseBase):
    pass

class CourseUpdate(BaseModel):
    name: str | None = None

class CourseResponse(CourseBase):
    courseId: UUID

    model_config = ConfigDict(from_attributes=True)

class CourseWithUsers(CourseResponse):
    users: list[UserResponse] = []

    model_config = ConfigDict(from_attributes=True)

# ----------------------------------------------------------------
# APP SCHEMAS
# ----------------------------------------------------------------
class AppBase(BaseModel):
    name: str
    description: str | None = None
    git_link: str | None = None

class AppCreate(AppBase):
    pass

class AppUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    git_link: str | None = None
    image: bytes | None = None

class AppResponse(AppBase):
    appId: UUID
    userId: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class AppWithUser(AppResponse):
    user: UserResponse

    model_config = ConfigDict(from_attributes=True)

class AppWithVersions(AppWithUser):
    versions: list[dict[str, str]] = []

    model_config = ConfigDict(from_attributes=True)

# ----------------------------------------------------------------
# DEPLOYMENT SCHEMAS
# ----------------------------------------------------------------
class Team(BaseModel):
    name: str
    userIds: list[str] = []

class DeploymentBase(BaseModel):
    name: str
    appId: UUID

class DeploymentCreate(DeploymentBase):
    releaseTag: str | None = None
    userInputVar: dict[str, Any] | None = None
    teams: list[Team] = []

class DeploymentResponse(DeploymentBase):
    deploymentId: UUID
    userId: UUID
    releaseTag: str | None = None
    userInputVar: dict[str, Any] | None = None
    status: str | None = None  # From latest task
    created_at: datetime | None = None

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
    members: list[DeploymentTeamMember] = []

    model_config = ConfigDict(from_attributes=True)


# Task summary for deployment details
class TaskSummary(BaseModel):
    taskId: UUID
    type: TaskType
    status: TaskStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Terraform outputs parsed
class DeploymentOutputs(BaseModel):
    """Parsed Terraform outputs - structure depends on app"""
    raw: dict[str, Any] | None = None  # Full outputs as dict

    model_config = ConfigDict(from_attributes=True)


# Full deployment detail response
class DeploymentDetail(DeploymentWithRelations):
    """Full deployment details with teams, task info, and outputs"""
    teams: list[DeploymentTeamResponse] = []
    latest_task: TaskSummary | None = None
    outputs: DeploymentOutputs | None = None
    logs: str | None = None  # Optional: can be excluded for large logs

    model_config = ConfigDict(from_attributes=True)

# ----------------------------------------------------------------
# Task SCHEMAS
# ----------------------------------------------------------------
class TaskBase(BaseModel):
    deploymentId: UUID
    celeryTaskId: str
    type: TaskType
    status: TaskStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    logs: str | None = None
    tf_state: str | None = None
    outputs: str | None = None

class TaskCreate(TaskBase):
    pass

class TaskUpdate(BaseModel):
    status: TaskStatus | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    logs: str | None = None
    tf_state: str | None = None
    outputs: str | None = None

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
    userIds: list[UUID] = []
    courseIds: list[UUID] = []

class UserGroupResponse(UserGroupBase):
    userGroupId: UUID

    model_config = ConfigDict(from_attributes=True)

class UserGroupWithMembers(UserGroupResponse):
    users: list[UserResponse] = []
    courses: list[CourseResponse] = []

    model_config = ConfigDict(from_attributes=True)

# ----------------------------------------------------------------
# TEAM SCHEMAS
# ----------------------------------------------------------------
class TeamBase(BaseModel):
    name: str
    userGroupId: UUID

class TeamCreate(TeamBase):
    userIds: list[UUID] = []

class TeamUpdate(BaseModel):
    name: str | None = None

class TeamResponse(TeamBase):
    teamId: UUID

    model_config = ConfigDict(from_attributes=True)

class TeamWithMembers(TeamResponse):
    users: list[UserResponse] = []

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
