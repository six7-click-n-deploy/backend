from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Enum, LargeBinary
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base
import enum
import uuid

# ----------------------------------------------------------------
# ENUMS
# ----------------------------------------------------------------
class UserRole(str, enum.Enum):
    STUDENT = "student"
    TEACHER = "teacher"
    ADMIN = "admin"

class TaskType(str, enum.Enum):
    DEPLOY = "deploy"
    UPDATE = "update"
    DESTROY = "destroy"
    PAUSE = "pause"
    RESUME = "resume"

class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"

class OpenStackAuthType(str, enum.Enum):
    APPLICATION_CREDENTIAL = "v3applicationcredential"
    PASSWORD = "password"

# ----------------------------------------------------------------
# COURSE MODEL
# ----------------------------------------------------------------
class Course(Base):
    __tablename__ = "courses"
    
    courseId = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    
    # Relationships
    users = relationship("User", back_populates="course")

# ----------------------------------------------------------------
# USER MODEL
# ----------------------------------------------------------------
class User(Base):
    __tablename__ = "users"
    
    userId = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    keycloak_id = Column(String, unique=True, index=True, nullable=True)  # Keycloak User ID (sub)
    email = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, nullable=False)
    firstName = Column(String, nullable=True)
    lastName = Column(String, nullable=True)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.STUDENT)
    courseId = Column(UUID(as_uuid=True), ForeignKey("courses.courseId"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    course = relationship("Course", back_populates="users")
    apps = relationship("App", back_populates="user")
    deployments = relationship("Deployment", back_populates="user")
    user_to_deployments = relationship("UserToDeployment", back_populates="user")
    user_to_teams = relationship("UserToTeam", back_populates="user")
    openstack_credential = relationship(
        "UserOpenStackCredential",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

# ----------------------------------------------------------------
# APP MODEL
# ----------------------------------------------------------------
class App(Base):
    __tablename__ = "apps"
    
    appId = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    image = Column(LargeBinary, nullable=True)  # base64 als Binary speichern
    git_link = Column(String, nullable=True)
    userId = Column(UUID(as_uuid=True), ForeignKey("users.userId"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="apps")
    deployments = relationship("Deployment", back_populates="app")

# ----------------------------------------------------------------
# DEPLOYMENT MODEL
# ----------------------------------------------------------------
class Deployment(Base):
    __tablename__ = "deployments"
    
    deploymentId = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    releaseTag = Column(String, nullable=True)
    userInputVar = Column(Text, nullable=True)  # könnte auch JSON sein
    userId = Column(UUID(as_uuid=True), ForeignKey("users.userId"), nullable=False, index=True)
    appId = Column(UUID(as_uuid=True), ForeignKey("apps.appId"), nullable=False, index=True)

    # Relationships
    user = relationship("User", back_populates="deployments")
    app = relationship("App", back_populates="deployments")
    user_to_deployments = relationship(
        "UserToDeployment",
        back_populates="deployment",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    teams = relationship(
        "Team",
        back_populates="deployment",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    tasks = relationship(
        "Task",
        back_populates="deployment",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

# ----------------------------------------------------------------
# TASK MODEL
# ----------------------------------------------------------------
class Task(Base):
    __tablename__ = "tasks"

    taskId = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deploymentId = Column(UUID(as_uuid=True), ForeignKey("deployments.deploymentId", ondelete="CASCADE"), nullable=False, index=True)
    celeryTaskId = Column(String, nullable=True)
    type = Column(Enum(TaskType), nullable=False)
    status = Column(Enum(TaskStatus), default=TaskStatus.PENDING)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    logs = Column(Text, nullable=True)  # JSON oder Text
    tf_state = Column(Text, nullable=True)  # Terraform State als JSON/Text
    outputs = Column(Text, nullable=True)  # Terraform Outputs als JSON/Text
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    deployment = relationship("Deployment", back_populates="tasks")
    
# ----------------------------------------------------------------
# USERTODEPLOYMENT MODEL
# ----------------------------------------------------------------
class UserToDeployment(Base):
    __tablename__ = "user_to_deployments"
    
    userToDeploymentId = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    userId = Column(UUID(as_uuid=True), ForeignKey("users.userId", ondelete="CASCADE"), nullable=False, index=True)
    deploymentId = Column(UUID(as_uuid=True), ForeignKey("deployments.deploymentId", ondelete="CASCADE"), nullable=False, index=True)
    
    # Relationships
    user = relationship("User", back_populates="user_to_deployments")
    deployment = relationship("Deployment", back_populates="user_to_deployments")

# ----------------------------------------------------------------
# TEAM MODEL
# ----------------------------------------------------------------
class Team(Base):
    __tablename__ = "teams"
    
    teamId = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    deploymentId = Column(UUID(as_uuid=True), ForeignKey("deployments.deploymentId", ondelete="CASCADE"), nullable=False, index=True)

    # Relationships
    deployment = relationship("Deployment", back_populates="teams")
    user_to_teams = relationship(
        "UserToTeam",
        back_populates="team",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

# ----------------------------------------------------------------
# USERTOTEAM MODEL
# ----------------------------------------------------------------
class UserToTeam(Base):
    __tablename__ = "user_to_teams"

    userToTeamId = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    userId = Column(UUID(as_uuid=True), ForeignKey("users.userId", ondelete="CASCADE"), nullable=False, index=True)
    teamId = Column(UUID(as_uuid=True), ForeignKey("teams.teamId", ondelete="CASCADE"), nullable=False, index=True)

    # Relationships
    user = relationship("User", back_populates="user_to_teams")
    team = relationship("Team", back_populates="user_to_teams")


# ----------------------------------------------------------------
# USER OPENSTACK CREDENTIAL MODEL
# ----------------------------------------------------------------
class UserOpenStackCredential(Base):
    __tablename__ = "user_openstack_credentials"

    credentialId = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    userId = Column(
        UUID(as_uuid=True),
        ForeignKey("users.userId", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    auth_type = Column(
        Enum(
            OpenStackAuthType,
            name="openstackauthtype",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )

    # Non-secret display fields (plaintext)
    auth_url = Column(String, nullable=False)
    region_name = Column(String, nullable=True)
    interface = Column(String, nullable=True, default="public")
    identity_api_version = Column(String, nullable=True, default="3")
    project_id = Column(String, nullable=True)
    project_name = Column(String, nullable=True)
    user_domain_name = Column(String, nullable=True)
    project_domain_name = Column(String, nullable=True)

    # Encrypted (Fernet ciphertext) — never logged, never returned via API
    encrypted_identifier = Column(LargeBinary, nullable=False)
    encrypted_secret = Column(LargeBinary, nullable=False)

    last_validated_at = Column(DateTime, nullable=True)
    last_validation_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="openstack_credential")

