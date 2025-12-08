from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Enum
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base
import enum

# ----------------------------------------------------------------
# ENUMS
# ----------------------------------------------------------------
class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"

# ----------------------------------------------------------------
# USER MODEL
# ----------------------------------------------------------------
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    git_repos = relationship("GitRepository", back_populates="owner")
    tasks = relationship("Task", back_populates="user")

# ----------------------------------------------------------------
# GIT REPOSITORY MODEL
# ----------------------------------------------------------------
class GitRepository(Base):
    __tablename__ = "git_repositories"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    url = Column(String, nullable=False)
    branch = Column(String, default="main")
    last_commit = Column(String, nullable=True)
    last_cloned_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    owner = relationship("User", back_populates="git_repos")

# ----------------------------------------------------------------
# TASK MODEL (für Celery Task Tracking)
# ----------------------------------------------------------------
class Task(Base):
    __tablename__ = "tasks"
    
    id = Column(Integer, primary_key=True, index=True)
    celery_task_id = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    task_type = Column(String, nullable=False)  # z.B. "git_clone", "terraform_apply"
    status = Column(Enum(TaskStatus), default=TaskStatus.PENDING)
    result = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="tasks")