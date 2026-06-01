from pydantic import BaseModel, EmailStr, Field, ConfigDict, model_validator
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID
from app.models import UserRole, TaskType, TaskStatus, OpenStackAuthType

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
    keycloak_id: Optional[str] = None
    firstName: Optional[str] = None
    lastName: Optional[str] = None
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
    userIds: List[str] = []

class DeploymentBase(BaseModel):
    name: str
    appId: UUID

class DeploymentCreate(DeploymentBase):
    releaseTag: Optional[str] = None
    userInputVar: Optional[Dict[str, Any]] = None
    teams: List[Team] = []

class DeploymentResponse(DeploymentBase):
    deploymentId: UUID
    userId: UUID
    releaseTag: Optional[str] = None
    userInputVar: Optional[Dict[str, Any]] = None
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

# ----------------------------------------------------------------
# OPENSTACK CREDENTIAL SCHEMAS
# ----------------------------------------------------------------
class OpenStackCredentialBase(BaseModel):
    """Non-secret connection metadata. Mirrors the `clouds.yaml` shape."""
    auth_type: OpenStackAuthType
    auth_url: str
    region_name: Optional[str] = None
    interface: Optional[str] = "public"
    identity_api_version: Optional[str] = "3"
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    user_domain_name: Optional[str] = None
    project_domain_name: Optional[str] = None


class OpenStackCredentialUpsert(OpenStackCredentialBase):
    """Body of PUT /me/openstack-credentials.

    `identifier` is either a username (password auth) or an
    application-credential ID. `secret` is the corresponding password or
    application-credential secret. Both are stored encrypted at rest.
    """
    identifier: str = Field(..., min_length=1, description="username OR application-credential ID")
    secret: str = Field(..., min_length=1, description="password OR application-credential secret")

    @model_validator(mode="after")
    def _validate_required_fields(self) -> "OpenStackCredentialUpsert":
        if self.auth_type == OpenStackAuthType.PASSWORD:
            if not self.user_domain_name:
                raise ValueError("user_domain_name is required for password auth")
            if not (self.project_id or self.project_name):
                raise ValueError("project_id or project_name is required for password auth")
        # application-credential auth: project_id is recommended but not required;
        # the credential itself carries the project scope.
        return self


class OpenStackCredentialFromYaml(BaseModel):
    """Convenience body: paste/upload a `clouds.yaml` and let the server pick it apart."""
    clouds_yaml: str = Field(..., min_length=1, description="raw clouds.yaml file contents")
    cloud_name: Optional[str] = Field(
        None, description="Pick a specific cloud from the YAML; required when there is more than one"
    )


class OpenStackCredentialResponse(OpenStackCredentialBase):
    """Masked response — never returns identifier/secret material.

    When `has_credential` is False, base fields are absent/empty and the
    consumer should branch on `has_credential`. `is_locked` and
    `active_deployments` are populated regardless so the frontend can
    render the appropriate guard UI even before any credential exists.
    """
    auth_type: Optional[OpenStackAuthType] = None  # type: ignore[assignment]
    auth_url: Optional[str] = None  # type: ignore[assignment]
    has_credential: bool = True
    last_validated_at: Optional[datetime] = None
    last_validation_error: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    is_locked: bool = False
    active_deployments: int = 0

    model_config = ConfigDict(from_attributes=True)
