# 🐳 Docker Setup - Quick Reference

## 📁 Docker Files Overview

```
backend/
├── Dockerfile                    # Production multi-stage build
├── Dockerfile.dev               # Development with hot reload
├── docker-compose.prod.yml      # Production setup
├── docker-compose.dev.yml       # Development setup
├── .dockerignore                # Files to exclude from build
├── .env.example                 # Environment template
├── start.sh                     # Production startup script
├── Makefile                     # Convenience commands
└── DOCKER.md                    # Detailed documentation
```

## 🚀 Quick Start

### Development
```bash
# One-time setup
cp .env.example .env
nano .env

# Start everything
make dev-up

# Access services
# API:     http://localhost:8000
# Docs:    http://localhost:8000/docs
# pgAdmin: http://localhost:5050
```

### Production
```bash
# Setup
make env
nano .env  # Add production values

# Deploy
make prod-build
make prod-up
make migrate

# Verify
make health
```

## 🔧 Common Commands

```bash
# Development
make dev-up          # Start dev environment
make dev-down        # Stop dev environment
make dev-logs        # View logs

# Production
make prod-up         # Start production
make prod-down       # Stop production
make prod-build      # Rebuild images

# Database
make migrate         # Run migrations
make db-backup       # Backup database
make db-shell        # Open psql shell

# Code Quality
make test            # Run tests
make lint            # Check code
make format          # Format code

# Utilities
make shell           # Container shell
make logs            # View logs
make health          # Check health
make secret          # Generate secret key
```

## 📦 Docker Images

### Production Image Features
- **Multi-stage build** - Smaller final image
- **Non-root user** - Security best practice
- **Health checks** - Auto-recovery
- **Environment variables** - 12-factor app
- **Automatic migrations** - On startup
- **Optimized caching** - Faster builds

### Development Image Features
- **Hot reload** - Code changes auto-restart
- **Source mounting** - Edit code locally
- **Dev tools included** - pytest, ruff, mypy
- **pgAdmin included** - Database UI

## 🔐 Environment Variables

### Required (Production)
```bash
SECRET_KEY=<generate-with-make-secret>
DATABASE_URL=postgresql://user:pass@postgres:5432/db
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1
```

### Optional
```bash
DEBUG=False
WORKERS=4
CORS_ORIGINS=https://yourdomain.com
LOG_LEVEL=info
```

## 🏗️ Architecture

```
┌─────────────────────────────────────────────┐
│              Reverse Proxy                  │
│            (Nginx/Traefik)                  │
└───────────────┬─────────────────────────────┘
                │
┌───────────────▼─────────────────────────────┐
│          Backend API (FastAPI)              │
│          - 4 Uvicorn Workers                │
│          - Non-root User                    │
│          - Health Checks                    │
└───────┬────────────────────┬────────────────┘
        │                    │
┌───────▼────────┐  ┌────────▼────────┐
│   PostgreSQL   │  │   Redis         │
│   - Volume     │  │   - Volume      │
│   - Health     │  │   - Persistence │
└────────────────┘  └─────────────────┘
```

## 🔄 Deployment Workflow

### Initial Deployment
1. Clone repository
2. Create `.env` file
3. Generate `SECRET_KEY`
4. Build images: `make prod-build`
5. Start services: `make prod-up`
6. Run migrations: `make migrate`
7. Verify: `make health`

### Updates
1. Pull changes: `git pull`
2. Rebuild: `make prod-build`
3. Restart: `make prod-up`
4. Migrate: `make migrate`

### Rollback
1. Stop services: `make prod-down`
2. Checkout previous version: `git checkout <tag>`
3. Rebuild: `make prod-build`
4. Start: `make prod-up`
5. Downgrade migration if needed

## 🐛 Debugging

### View Logs
```bash
make logs                    # All services
make logs | grep ERROR       # Errors only
docker logs backend-api      # Specific container
```

### Container Shell
```bash
make shell                   # Backend shell
make db-shell               # Database shell
make redis-cli              # Redis CLI
```

### Check Health
```bash
make health                  # API health
docker compose ps            # All services
docker stats                 # Resource usage
```

## 📊 Monitoring

### Built-in
- Health check endpoint: `/health`
- Docker health checks
- Container logs

### Recommended Tools
- **Prometheus** + **Grafana** - Metrics
- **Sentry** - Error tracking
- **Datadog** - APM
- **ELK Stack** - Log aggregation

## 🔒 Security

### Best Practices Implemented
✅ Non-root user in container  
✅ Multi-stage build (smaller attack surface)  
✅ Minimal base image (Python slim)  
✅ No secrets in Dockerfile  
✅ Health checks enabled  
✅ Resource limits available  
✅ Security scanning ready  

### Additional Recommendations
- Use Docker secrets in production
- Enable TLS/HTTPS
- Regular image updates
- Vulnerability scanning
- Network isolation
- Backup strategy

## 📚 Documentation

- [DOCKER.md](DOCKER.md) - Complete Docker guide
- [MIGRATIONS.md](MIGRATIONS.md) - Database migrations
- [.env.example](.env.example) - All environment variables
- [README.md](README.md) - Main documentation

## 🆘 Support

### Common Issues

**Port already in use**
```bash
# Change in .env
API_PORT=8001
```

**Database connection failed**
```bash
# Check if postgres is running
docker compose ps
# View postgres logs
docker compose logs postgres
```

**Migration failed**
```bash
# Rollback one migration
make migration-downgrade
# Or reset (⚠️ data loss)
make db-reset
```

**Container won't start**
```bash
# Check logs
make logs
# Rebuild from scratch
docker compose down -v
make prod-build
make prod-up
```

---

**For detailed documentation, see [DOCKER.md](DOCKER.md)**
