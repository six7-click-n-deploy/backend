# Backend Service

> ⚠️ **Important**: This is the backend application code repository. For deployment and orchestration, use the [`deployment`](https://github.com/six7-click-n-deploy/deployment) repository.

FastAPI backend service with authentication, Git operations, and Celery task queue integration.

## 🚀 Quick Start

**Don't start from this repository!** Use the deployment repository:

```bash
# 1. Clone all repos
git clone https://github.com/six7-click-n-deploy/deployment.git
git clone https://github.com/six7-click-n-deploy/backend.git
git clone https://github.com/six7-click-n-deploy/frontend.git
git clone https://github.com/six7-click-n-deploy/worker.git

# 2. Start development environment
cd deployment
make env && make dev-up

# 3. Access services
# Backend:  http://localhost:8000
# API Docs: http://localhost:8000/docs
```

See [`DEVELOPMENT.md`](DEVELOPMENT.md) for detailed development guide.

## 📁 Verzeichnisstruktur

```
backend/
├── main.py                      # FastAPI Application Entry Point
├── config.py                    # Settings & Environment Config
├── database.py                  # Database Connection & Session
├── models.py                    # SQLAlchemy Database Models
├── schemas.py                   # Pydantic Schemas (Request/Response)
├── pyproject.toml               # Project Configuration & Dependencies
├── alembic.ini                  # Alembic Configuration
├── Dockerfile                   # Docker Image Definition
├── .env                         # Environment Variables (nicht committen!)
├── .env.example                 # Environment Template
├── .gitignore                   # Git Ignore File
│
├── alembic/                     # Database Migrations
│   ├── env.py                   # Alembic Environment
│   ├── script.py.mako           # Migration Template
│   ├── README.md                # Migration Documentation
│   └── versions/                # Migration Files
│
├── routers/                     # API Endpoints
│   ├── __init__.py
│   ├── auth.py                  # Authentication (Register/Login)
│   ├── users.py                 # User Management
│   ├── git_operations.py        # Git Repository Operations
│   └── tasks.py                 # Celery Task Management
│
├── services/                    # Business Logic Layer
│   ├── __init__.py
│   ├── git_service.py           # Git Clone/Pull/Info Operations
│   └── celery_client.py         # Celery Client (Task Sender)
│
└── utils/                       # Utility Functions
    ├── __init__.py
    └── auth.py                  # Auth Helpers (JWT, Password Hash)
```

## 🔧 Komponenten

### **main.py**
- FastAPI Application
- Router Registration
- CORS Middleware
- Lifespan Events (DB Setup)

### **config.py**
- Pydantic Settings
- Environment Variable Loading
- Configuration Management

### **database.py**
- SQLAlchemy Engine
- Session Management
- Database Dependency

### **models.py**
- User Model
- GitRepository Model
- Task Model (für Celery Tracking)

### **schemas.py**
- Pydantic Models für Request/Response
- Data Validation
- Type Safety

### **routers/**
- **auth.py**: `/auth/register`, `/auth/login`
- **users.py**: `/users/me`, `/users/{id}`
- **git_operations.py**: `/git/repos`, `/git/clone`
- **tasks.py**: `/tasks/`, `/tasks/{id}`

### **services/**
- **git_service.py**: Git Operations (Clone, Pull, Info)
- **celery_client.py**: Celery Task Sending & Status

### **utils/**
- **auth.py**: JWT Token, Password Hashing, User Authentication

## 🚀 API Endpoints

### Authentication
```
POST   /auth/register          # User registrieren
POST   /auth/login             # User login
```

### Users
```
GET    /users/me               # Aktueller User
GET    /users/{id}             # User by ID
```

### Git Operations
```
POST   /git/repos              # Repository hinzufügen
GET    /git/repos              # Alle Repos auflisten
GET    /git/repos/{id}         # Repo by ID
POST   /git/clone              # Repository klonen (direkt)
DELETE /git/repos/{id}         # Repository löschen
```

### Tasks (Celery)
```
POST   /tasks/                 # Task erstellen (an Worker senden)
GET    /tasks/                 # Alle User Tasks
GET    /tasks/{id}             # Task Status
GET    /tasks/celery/{task_id} # Celery Task Status
```

### System
```
GET    /health                 # Health Check
GET    /                       # API Info
GET    /docs                   # Swagger UI (automatisch)
```

## 🔐 Authentication Flow

1. User registriert sich: `POST /auth/register`
2. Server erstellt User & gibt JWT Token zurück
3. Client speichert Token
4. Bei jedem Request: `Authorization: Bearer <token>` Header
5. Backend validiert Token und gibt User zurück

## 🐙 Git Integration

### Direkte Nutzung (ohne Celery)
```python
POST /git/clone
{
  "repo_id": 1
}
```

### Mit Celery Worker
Der Backend **sendet** nur Tasks an den Worker:
```python
# Im Backend
celery_task_id = send_git_clone_task(repo_url, branch, repo_id)
```

Der **Worker Service** (separates Repo) führt dann aus:
```python
# Im Worker Service
@celery_app.task
def clone_repository(repo_url, branch, repo_id):
    # Git clone operation
    ...
```

## 🔄 Celery Integration

Das Backend ist **Client**, nicht Worker:
- Sendet Tasks via `celery_app.send_task()`
- Fragt Status ab via `celery_app.AsyncResult()`
- Führt **keine** Tasks aus

Der Worker Service (separates Repo):
- Empfängt Tasks von Redis
- Führt Git/Terraform Operations aus
- Speichert Results in Redis

## 🗄️ Database Models

### User
- id, email, username, hashed_password
- created_at, updated_at
- Relationships: git_repos, tasks

### GitRepository
- id, user_id, name, url, branch
- last_commit, last_cloned_at
- Relationship: owner (User)

### Task
- id, celery_task_id, user_id
- task_type, status, result, error
- Relationship: user (User)

## 🛡️ Security

- **Passwords**: Bcrypt Hashing
- **Authentication**: JWT Bearer Tokens
- **Authorization**: User-specific data access
- **CORS**: Configurable origins
- **SQL Injection**: SQLAlchemy ORM Protection

## 🐳 Docker Setup

```bash
# Build
docker build -t backend-api .

# Run
docker run -p 8000:8000 --env-file .env backend-api
```

Oder mit docker-compose:
```bash
docker-compose up backend
```

## � Docker Deployment

### Quick Start with Docker

```bash
# 1. Create environment file
make env  # or: cp .env.example .env

# 2. Edit .env with your values
nano .env

# 3. Start development environment
make dev-up

# 4. Run migrations
make migrate-dev

# 5. View logs
make dev-logs
```

### Production Deployment

```bash
# 1. Build production images
make prod-build

# 2. Start production environment
make prod-up

# 3. Run migrations
make migrate

# 4. Check health
make health
```

### Available Make Commands

```bash
make help              # Show all available commands
make dev-up            # Start development environment
make prod-up           # Start production environment
make logs              # View logs
make shell             # Open shell in container
make migrate           # Run database migrations
make test              # Run tests
make lint              # Run linter
make format            # Format code
```

See [DOCKER.md](DOCKER.md) for detailed Docker documentation.

## �📦 Dependencies

- **FastAPI**: Web Framework
- **SQLAlchemy**: ORM & Database Models
- **Alembic**: Database Migrations
- **PostgreSQL**: Database
- **Celery**: Task Queue (Client)
- **Redis**: Message Broker
- **GitPython**: Git Operations
- **python-jose**: JWT Tokens
- **passlib**: Password Hashing
- **Pytest**: Testing Framework
- **Ruff**: Linting & Formatting

## 🧪 Testing

```bash
# With Docker
make test

# Locally
pip install -e ".[dev]"
pytest

# With Coverage
pytest --cov=. --cov-report=html
```

## 🔍 Code Quality

```bash
# Linting
make lint           # Check code
make lint-fix       # Fix issues

# Formatting
make format         # Format code

# Type Checking
make type-check     # Run mypy
```

## 📝 Environment Variables

Create `.env` from template:
```bash
cp .env.example .env
```

**Required Variables**:
- `SECRET_KEY` - Generate with: `make secret`
- `DATABASE_URL` - PostgreSQL connection string
- `CELERY_BROKER_URL` - Redis connection for Celery
- `CELERY_RESULT_BACKEND` - Redis connection for results

See [.env.example](.env.example) for all variables.

## �️ Database Migrations

```bash
# Create new migration
make migration-create MSG="Add new field"

# Apply migrations
make migrate

# Show current revision
make migration-current

# Show history
make migration-history

# Rollback one migration
make migration-downgrade
```

See [MIGRATIONS.md](MIGRATIONS.md) for detailed migration guide.

## 🚦 Startup Order

1. **PostgreSQL** - Database service
2. **Redis** - Message broker for Celery
3. **Backend API** - FastAPI application
4. **Celery Worker** - Background task processor (optional)

With Docker Compose, health checks ensure correct startup order.

## 📚 Documentation

- **[DEVELOPMENT.md](DEVELOPMENT.md)** - Development workflow guide ⭐ 
- **[MIGRATIONS.md](MIGRATIONS.md)** - Database migrations guide
- **[DOCKER.md](DOCKER.md)** - Docker image details
- **[deployment/README.md](https://github.com/six7-click-n-deploy/deployment)** - Orchestration ⭐
- **[alembic/README.md](alembic/README.md)** - Alembic-specific docs
- **API Docs**: http://localhost:8000/docs (when running)

## 🔗 Multi-Repository Architecture

This backend is part of a microservices architecture:

```
┌─────────────────────────────────────────┐
│         deployment (orchestration)       │  ⭐ Start here!
│  - docker-compose.dev.yml               │
│  - docker-compose.prod.yml              │
│  - Makefile (all commands)              │
└──────────────┬──────────────────────────┘
               │
    ┌──────────┼──────────┬────────────┐
    │          │          │            │
┌───▼────┐ ┌──▼──────┐ ┌─▼──────┐ ┌──▼─────┐
│frontend│ │ backend │ │ worker │ │ infra  │
│        │ │  (API)  │ │(Celery)│ │(PG+Redis)
└────────┘ └─────────┘ └────────┘ └────────┘
```

**Related Repositories:**
- [`deployment`](https://github.com/six7-click-n-deploy/deployment) - Docker Compose & Makefile
- [`frontend`](https://github.com/six7-click-n-deploy/frontend) - React/Next.js UI
- [`worker`](https://github.com/six7-click-n-deploy/worker) - Celery worker

**Key Points:**
- Each service has its own repository
- `deployment` repo orchestrates all services
- Development uses hot reload (source mounted)
- Production uses pre-built images from GHCR
- Separate networks for service isolation