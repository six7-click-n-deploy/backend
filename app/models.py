import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


# ----------------------------------------------------------------
# ENUMS
# ----------------------------------------------------------------
class UserRole(str, enum.Enum):
    STUDENT = "student"
    TEACHER = "teacher"
    ADMIN = "admin"


class AppVersionApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class TaskType(str, enum.Enum):
    DEPLOY = "deploy"
    UPDATE = "update"
    DESTROY = "destroy"
    PAUSE = "pause"
    RESUME = "resume"
    # Single-resource redeploy: ``terraform apply -replace=<addr> -target=<addr>``
    # for ONE Compute-Instance, without touching the other team VMs of the
    # same deployment. Backend-validates that the targeted address is a
    # known compute-instance in the cached TF state.
    REDEPLOY = "redeploy"


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
    # Many-to-many relationship to the User table via the
    # ``course_teachers`` join table. A given course can have several
    # teachers, and a given teacher can own several courses. This is
    # the data backing the ``is_course_teacher`` capability check —
    # see :mod:`app.utils.capabilities`. Phase 1 only defines the
    # schema; backfill of the existing teacher-per-course relationship
    # happens in Phase 3.
    course_teachers = relationship(
        "CourseTeacher",
        back_populates="course",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


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
    course_teacher_links = relationship(
        "CourseTeacher",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
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
    image = Column(LargeBinary, nullable=True)  # raw bytes of the uploaded logo
    image_mime = Column(String(64), nullable=True)  # e.g. "image/png" — needed to build a data-URL on read
    git_link = Column(String, nullable=True)
    is_private = Column(Boolean, nullable=False, default=False)
    userId = Column(UUID(as_uuid=True), ForeignKey("users.userId"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Soft-delete marker. When set, the app is hidden from default
    # queries (apps list, deploy wizard) but the row stays so existing
    # deployments referencing this app keep their FK valid and the
    # audit trail survives. The router refuses to soft-delete an app
    # while it has live deployments to avoid orphan resources.
    deleted_at = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", back_populates="apps")
    deployments = relationship("Deployment", back_populates="app")
    version_approvals = relationship(
        "AppVersionApproval",
        back_populates="app",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


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
    # Soft-delete marker. Set to ``utcnow()`` to hide the deployment
    # from default queries while keeping the row for audit/restore.
    # See lifecycle.py — DELETE is only allowed in terminal states, so
    # by the time this is set the OpenStack resources are already gone
    # (or never existed). The index on (deleted_at IS NULL) keeps the
    # default list query cheap.
    deleted_at = Column(DateTime, nullable=True)

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
    deploymentId = Column(
        UUID(as_uuid=True),
        ForeignKey("deployments.deploymentId", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    celeryTaskId = Column(String, nullable=True)
    type = Column(Enum(TaskType), nullable=False)
    status = Column(Enum(TaskStatus), default=TaskStatus.PENDING)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    logs = Column(Text, nullable=True)  # JSON oder Text
    tf_state = Column(Text, nullable=True)  # Terraform State als JSON/Text
    outputs = Column(Text, nullable=True)  # Terraform Outputs als JSON/Text
    # Live-progress columns. Updated by the celery event listener whenever
    # the worker emits a `task-progress` event. They are advisory — the
    # canonical source of "what is happening right now" is the SSE stream;
    # these columns let users who reload the page see the last known
    # phase/percent without waiting for the next event.
    current_phase = Column(String(50), nullable=True)
    progress_pct = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    deployment = relationship("Deployment", back_populates="tasks")


# ----------------------------------------------------------------
# USERTODEPLOYMENT MODEL
# ----------------------------------------------------------------
class UserToDeployment(Base):
    __tablename__ = "user_to_deployments"

    userToDeploymentId = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    userId = Column(
        UUID(as_uuid=True),
        ForeignKey("users.userId", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    deploymentId = Column(
        UUID(as_uuid=True),
        ForeignKey("deployments.deploymentId", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

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
    deploymentId = Column(
        UUID(as_uuid=True),
        ForeignKey("deployments.deploymentId", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

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
    userId = Column(
        UUID(as_uuid=True),
        ForeignKey("users.userId", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    teamId = Column(
        UUID(as_uuid=True),
        ForeignKey("teams.teamId", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

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


# ----------------------------------------------------------------
# APP VERSION APPROVAL MODEL
# ----------------------------------------------------------------
class AppVersionApproval(Base):
    __tablename__ = "app_version_approvals"

    approvalId = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    appId = Column(
        UUID(as_uuid=True),
        ForeignKey("apps.appId", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_tag = Column(String, nullable=False)
    status = Column(
        Enum(AppVersionApprovalStatus),
        nullable=False,
        default=AppVersionApprovalStatus.PENDING,
    )
    diff_url = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    reviewed_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.userId", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("appId", "version_tag", name="uq_app_version_approval"),
    )

    # Relationships
    app = relationship("App", back_populates="version_approvals")
    reviewer = relationship("User", foreign_keys=[reviewed_by])


# ----------------------------------------------------------------
# COURSE TEACHER MODEL
# ----------------------------------------------------------------
# Many-to-many join between courses and users. A row ``(course_id,
# user_id)`` declares that ``user`` is one of the teachers responsible
# for ``course``. Backs the per-course "course-teacher" capability
# (inspect-only on deployments in the course, edit/delete on the
# course itself). The composite primary key gives us natural
# idempotency on insert and dedupes accidental double-adds.
#
# Pattern note: this mirrors the UserToTeam shape — a tiny join model
# with two FKs plus a single composite PK — except both FKs together
# *are* the PK here (instead of an extra synthetic UUID column),
# because we never need to address a single membership row by id.
class CourseTeacher(Base):
    __tablename__ = "course_teachers"

    courseId = Column(
        "course_id",
        UUID(as_uuid=True),
        ForeignKey("courses.courseId", ondelete="CASCADE"),
        primary_key=True,
    )
    userId = Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey("users.userId", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )

    # Relationships
    course = relationship("Course", back_populates="course_teachers")
    user = relationship("User", back_populates="course_teacher_links")
