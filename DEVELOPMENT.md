# Backend Development Guide

This backend service is part of a multi-repo architecture. **Deployment and orchestration is managed in the `deployment` repository.**

## 📁 Repository Structure

```
├── backend/          # ← You are here (application code)
├── frontend/         # Frontend application
├── worker/          # Celery worker service
└── deployment/      # Docker Compose & orchestration (START HERE!)
```

## 🚀 Quick Start

**⚠️ Don't start services from this repo!** Use the `deployment` repository instead.

### 1. Clone All Repos

```bash
# Clone all repositories in the same parent directory
git clone https://github.com/six7-click-n-deploy/frontend.git
git clone https://github.com/six7-click-n-deploy/backend.git
git clone https://github.com/six7-click-n-deploy/worker.git
git clone https://github.com/six7-click-n-deploy/deployment.git
```

### 2. Start Development Environment

```bash
cd deployment
make env              # Create .env file
make secret          # Generate SECRET_KEY
# Edit .env with your values
make dev-up          # Start all services
make migrate-dev     # Run database migrations
```

See `deployment/README.md` for complete documentation.

## 🔧 Development Workflow

### Hot Reload

The development environment automatically reloads when you edit code in this repository. Just save your files and the backend will restart.

### Running Backend Locally (Optional)

If you want to run just the backend locally without Docker:

```bash
# Install dependencies
pip install -e ".[dev]"

# Create .env file
cp .env.example .env
# Edit .env with local database connection

# Run migrations
alembic upgrade head

# Start server
uvicorn main:app --reload
```

## 📋 Backend Structure

```
backend/
├── main.py                    # FastAPI application
├── config.py                  # Settings & environment
├── database.py                # Database connection
├── models.py                  # SQLAlchemy models
├── schemas.py                 # Pydantic schemas
├── Dockerfile                 # Production image
├── Dockerfile.dev             # Development image
├── start.sh                   # Production startup script
├── pyproject.toml             # Dependencies & config
├── alembic.ini               # Migration config
│
├── alembic/                   # Database migrations
│   ├── env.py
│   └── versions/
│
├── routers/                   # API endpoints
│   ├── auth.py               # Authentication
│   ├── users.py              # User management
│   ├── git_operations.py     # Git operations
│   └── tasks.py              # Task management
│
├── services/                  # Business logic
│   ├── celery_client.py      # Celery client
│   └── git_service.py        # Git operations
│
└── utils/                     # Utilities
    └── auth.py               # Auth helpers
```

## 🗄️ Database Migrations

### Create Migration

```bash
# From deployment repo
cd ../deployment
make migration-create MSG="Add new field to users"
```

### Apply Migrations

```bash
# Development
cd ../deployment
make migrate-dev

# Production
make migrate-prod
```

See `MIGRATIONS.md` for detailed migration documentation.

## 🧪 Testing

### Run Tests

```bash
# From deployment repo
cd ../deployment
make test-backend

# With coverage
make test-backend-cov
```

### Local Testing

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# With coverage
pytest --cov=. --cov-report=html
```

## 🔍 Code Quality

### Linting & Formatting

```bash
# From deployment repo
cd ../deployment
make lint-backend        # Check code
make lint-backend-fix    # Auto-fix issues
make format-backend      # Format code
```

### Local Linting

```bash
# Check code
ruff check .

# Fix issues
ruff check --fix .

# Format code
ruff format .

# Type checking
mypy .
```

## 📦 Dependencies

Managed via `pyproject.toml`:

```bash
# Install production dependencies
pip install -e .

# Install with dev dependencies
pip install -e ".[dev]"
```

**Main Dependencies:**
- FastAPI - Web framework
- SQLAlchemy - ORM
- Alembic - Database migrations
- Celery - Task queue client
- PostgreSQL (psycopg2) - Database driver
- Redis - Message broker
- GitPython - Git operations
- python-jose - JWT tokens
- passlib - Password hashing

**Dev Dependencies:**
- pytest - Testing framework
- ruff - Linting & formatting
- mypy - Type checking
- httpx - HTTP client for tests

## 🐳 Docker Images

### Development Image (Dockerfile.dev)
- Hot reload enabled
- Source code mounted from host
- Dev dependencies included
- Debug mode enabled

### Production Image (Dockerfile)
- Multi-stage build
- Minimal dependencies
- Non-root user
- Optimized for size & security
- Auto-runs migrations on startup

**Images are built by CI/CD** and pushed to GitHub Container Registry:
- `ghcr.io/six7-click-n-deploy/backend:latest`

## 🔐 Environment Variables

See `.env.example` for all available variables.

**Required:**
- `DATABASE_URL` - PostgreSQL connection
- `SECRET_KEY` - JWT signing key (32+ chars)
- `CELERY_BROKER_URL` - Redis for Celery
- `CELERY_RESULT_BACKEND` - Redis for results

**Optional:**
- `DEBUG` - Debug mode (default: False)
- `CORS_ORIGINS` - Allowed origins
- `ACCESS_TOKEN_EXPIRE_MINUTES` - Token lifetime
- `WORKERS` - Uvicorn workers (default: 4)

## 📚 API Documentation

When running, interactive API docs are available at:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## 🔄 Workflow

### Adding a New Feature

1. Edit code in `backend/` directory
2. Hot reload automatically restarts the service
3. Test your changes
4. Create migration if models changed:
   ```bash
   cd ../deployment
   make migration-create MSG="Your migration message"
   make migrate-dev
   ```
5. Commit and push changes
6. CI/CD builds and pushes new image

### Updating Dependencies

1. Edit `pyproject.toml`
2. Rebuild dev image:
   ```bash
   cd ../deployment
   make dev-build-backend
   make dev-restart
   ```

## 🐛 Debugging

### View Logs

```bash
# From deployment repo
cd ../deployment
make dev-logs-backend    # Backend logs only
```

### Shell Access

```bash
cd ../deployment
make shell-backend       # Open bash in container
```

### Database Access

```bash
cd ../deployment
make shell-db           # PostgreSQL shell
```

## 🆘 Troubleshooting

### Import Errors

Check that all dependencies are installed:
```bash
pip install -e ".[dev]"
```

### Database Connection Errors

Ensure PostgreSQL is running:
```bash
cd ../deployment
make status
```

### Migration Errors

Check migration history:
```bash
cd ../deployment
make migration-history
make migration-current
```

## 📖 Additional Documentation

- **Deployment**: `../deployment/README.md` - **START HERE**
- **Migrations**: `MIGRATIONS.md` - Database migrations guide
- **Docker**: `DOCKER.md` - Docker deployment details
- **Alembic**: `alembic/README.md` - Migration specifics

## 🔗 Related Repositories

- Frontend: https://github.com/six7-click-n-deploy/frontend
- Worker: https://github.com/six7-click-n-deploy/worker
- Deployment: https://github.com/six7-click-n-deploy/deployment

## 🆘 Support

Issues: https://github.com/six7-click-n-deploy/backend/issues

---

**Remember**: Use the `deployment` repository for starting, stopping, and managing services!
