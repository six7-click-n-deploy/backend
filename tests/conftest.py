import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import App, User, UserRole
from app.utils.keycloak_auth import get_current_user_keycloak

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def mock_user(db):
    user = User(
        userId=uuid.uuid4(),
        keycloak_id="test-keycloak-id",
        email="test@dhbw.de",
        username="testuser",
        firstName="Test",
        lastName="User",
        role=UserRole.TEACHER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def mock_admin(db):
    user = User(
        userId=uuid.uuid4(),
        keycloak_id="admin-keycloak-id",
        email="admin@dhbw.de",
        username="adminuser",
        firstName="Admin",
        lastName="User",
        role=UserRole.ADMIN,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def mock_student(db):
    user = User(
        userId=uuid.uuid4(),
        keycloak_id="student-keycloak-id",
        email="student@dhbw.de",
        username="studentuser",
        firstName="Student",
        lastName="User",
        role=UserRole.STUDENT,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_client(user):
    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    def override_get_current_user():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user_keycloak] = override_get_current_user
    return TestClient(app)


@pytest.fixture
def client(mock_user):
    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    def override_get_current_user():
        return mock_user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user_keycloak] = override_get_current_user

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def admin_client(mock_admin):
    c = _make_client(mock_admin)
    yield c
    app.dependency_overrides.clear()


@pytest.fixture
def student_client(mock_student):
    c = _make_client(mock_student)
    yield c
    app.dependency_overrides.clear()


@pytest.fixture
def unauth_client():
    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


# ----------------------------------------------------------------
# SHARED DB HELPERS
# ----------------------------------------------------------------
def create_app_in_db(db, user, *, name="Test App", git_link="https://github.com/example/repo", is_private=False):
    db_app = App(
        appId=uuid.uuid4(),
        name=name,
        git_link=git_link,
        is_private=is_private,
        userId=user.userId,
    )
    db.add(db_app)
    db.commit()
    db.refresh(db_app)
    return db_app

