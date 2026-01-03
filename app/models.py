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
class DeploymentStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"

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

# ----------------------------------------------------------------
# COURSE MODEL
# ----------------------------------------------------------------
class Course(Base):
    __tablename__ = "courses"
    
    courseId = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    
    # Relationships
    users = relationship("User", back_populates="course")
    course_to_user_groups = relationship("CourseToUserGroup", back_populates="course")

# ----------------------------------------------------------------
# USER MODEL
# ----------------------------------------------------------------
class User(Base):
    __tablename__ = "users"
    
    userId = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, nullable=False)
    password = Column(String, nullable=False)  # hashed password
    role = Column(Enum(UserRole), nullable=False, default=UserRole.STUDENT)
    courseId = Column(UUID(as_uuid=True), ForeignKey("courses.courseId"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    course = relationship("Course", back_populates="users")
    apps = relationship("App", back_populates="user")
    deployments = relationship("Deployment", back_populates="user")
    user_to_user_groups = relationship("UserToUserGroup", back_populates="user")
    user_to_teams = relationship("UserToTeam", back_populates="user")

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
    userId = Column(UUID(as_uuid=True), ForeignKey("users.userId"), nullable=False)
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
    status = Column(Enum(DeploymentStatus), default=DeploymentStatus.PENDING)
    releaseTag = Column(String, nullable=True)
    userInputVar = Column(Text, nullable=True)  # könnte auch JSON sein
    userId = Column(UUID(as_uuid=True), ForeignKey("users.userId"), nullable=False)
    appId = Column(UUID(as_uuid=True), ForeignKey("apps.appId"), nullable=False)
    
    # Relationships
    user = relationship("User", back_populates="deployments")
    app = relationship("App", back_populates="deployments")
    user_group = relationship("UserGroup", back_populates="deployment", uselist=False)

# ----------------------------------------------------------------
# TASK MODEL
# ----------------------------------------------------------------
class Task(Base):
    __tablename__ = "tasks"

    taskId = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deploymentId = Column(UUID(as_uuid=True), ForeignKey("deployments.deploymentId"), nullable=False)
    celeryTaskId = Column(String, nullable=False)
    type = Column(Enum(TaskType), nullable=False)
    status = Column(Enum(TaskStatus), default=TaskStatus.PENDING)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    logs = Column(Text, nullable=True)  # JSON oder Text
    tf_state = Column(Text, nullable=True)  # Terraform State als JSON/Text
    outputs = Column(Text, nullable=True)  # Terraform Outputs als JSON/Text
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    deployment = relationship("Deployment", backref="tasks")
    
# ----------------------------------------------------------------
# USERGROUP MODEL
# ----------------------------------------------------------------
class UserGroup(Base):
    __tablename__ = "user_groups"
    
    userGroupId = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deploymentId = Column(UUID(as_uuid=True), ForeignKey("deployments.deploymentId"), unique=True, nullable=False)
    
    # Relationships
    deployment = relationship("Deployment", back_populates="user_group")
    user_to_user_groups = relationship("UserToUserGroup", back_populates="user_group")
    course_to_user_groups = relationship("CourseToUserGroup", back_populates="user_group")
    teams = relationship("Team", back_populates="user_group")

# ----------------------------------------------------------------
# USERTOUSERGROUP MODEL
# ----------------------------------------------------------------
class UserToUserGroup(Base):
    __tablename__ = "user_to_user_groups"
    
    userToUserGroupId = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    userId = Column(UUID(as_uuid=True), ForeignKey("users.userId"), nullable=False)
    userGroupId = Column(UUID(as_uuid=True), ForeignKey("user_groups.userGroupId"), nullable=False)
    
    # Relationships
    user = relationship("User", back_populates="user_to_user_groups")
    user_group = relationship("UserGroup", back_populates="user_to_user_groups")

# ----------------------------------------------------------------
# COURSETOUSERGROUP MODEL
# ----------------------------------------------------------------
class CourseToUserGroup(Base):
    __tablename__ = "course_to_user_groups"
    
    courseToUserGroupID = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    courseId = Column(UUID(as_uuid=True), ForeignKey("courses.courseId"), nullable=False)
    userGroupId = Column(UUID(as_uuid=True), ForeignKey("user_groups.userGroupId"), nullable=False)
    
    # Relationships
    course = relationship("Course", back_populates="course_to_user_groups")
    user_group = relationship("UserGroup", back_populates="course_to_user_groups")

# ----------------------------------------------------------------
# TEAM MODEL
# ----------------------------------------------------------------
class Team(Base):
    __tablename__ = "teams"
    
    teamId = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    userGroupId = Column(UUID(as_uuid=True), ForeignKey("user_groups.userGroupId"), nullable=False)
    
    # Relationships
    user_group = relationship("UserGroup", back_populates="teams")
    user_to_teams = relationship("UserToTeam", back_populates="team")

# ----------------------------------------------------------------
# USERTOTEAM MODEL
# ----------------------------------------------------------------
class UserToTeam(Base):
    __tablename__ = "user_to_teams"
    
    userToTeamId = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    userId = Column(UUID(as_uuid=True), ForeignKey("users.userId"), nullable=False)
    teamId = Column(UUID(as_uuid=True), ForeignKey("teams.teamId"), nullable=False)
    
    # Relationships
    user = relationship("User", back_populates="user_to_teams")
    team = relationship("Team", back_populates="user_to_teams")

