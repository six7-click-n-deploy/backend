# Backend Service - Projektstruktur

## 📁 Verzeichnisstruktur

```
backend/
├── main.py                      # FastAPI Application Entry Point
├── config.py                    # Settings & Environment Config
├── database.py                  # Database Connection & Session
├── models.py                    # SQLAlchemy Database Models
├── schemas.py                   # Pydantic Schemas (Request/Response)
├── requirements.txt             # Python Dependencies
├── Dockerfile                   # Docker Image Definition
├── .env                         # Environment Variables (nicht committen!)
├── .env.example                 # Environment Template
├── .gitignore                   # Git Ignore File
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

## 📦 Dependencies

- **FastAPI**: Web Framework
- **SQLAlchemy**: ORM
- **PostgreSQL**: Database
- **Celery**: Task Queue (Client)
- **Redis**: Message Broker
- **GitPython**: Git Operations
- **python-jose**: JWT Tokens
- **passlib**: Password Hashing

## 🧪 Testing

```bash
# Install dev dependencies
pip install pytest httpx

# Run tests
pytest

# Mit Coverage
pytest --cov=.
```

## 📝 Environment Variables

Siehe `.env.example` für alle benötigten Variables.

**Wichtig**: 
- `SECRET_KEY` muss mindestens 32 Zeichen haben
- `DATABASE_URL` muss auf PostgreSQL zeigen
- `CELERY_BROKER_URL` muss auf Redis zeigen

## 🚦 Startup

1. Database muss laufen (PostgreSQL)
2. Redis muss laufen (für Celery)
3. Backend startet und erstellt DB Tables automatisch
4. Swagger Docs verfügbar unter `/docs`

## 🔗 Integration mit anderen Services

- **Worker Service**: Empfängt Tasks via Celery
- **Frontend Service**: Konsumiert REST API
- **Database Service**: PostgreSQL Container
- **Redis Service**: Message Broker Container