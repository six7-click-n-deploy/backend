# Docker Deployment Guide

## 🐳 Production Deployment

### Prerequisites

1. Docker & Docker Compose installed
2. `.env` file configured (copy from `.env.example`)
3. SSL certificates (for HTTPS in production)

### Quick Start

```bash
# 1. Clone repository
git clone <repository-url>
cd backend

# 2. Create .env file from template
cp .env.example .env

# 3. Edit .env with your production values
nano .env  # or vim, code, etc.

# 4. Build and start services
docker compose -f docker-compose.prod.yml up -d

# 5. Check logs
docker compose -f docker-compose.prod.yml logs -f

# 6. Verify health
curl http://localhost:8000/health
```

## 🔧 Configuration

### Environment Variables

Create a `.env` file with these required variables:

```bash
# Security (REQUIRED!)
SECRET_KEY=your_secret_key_here

# Database
DATABASE_URL=postgresql://user:password@postgres:5432/backend_db

# Celery
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1

# CORS
CORS_ORIGINS=https://yourdomain.com,https://www.yourdomain.com

# Server
WORKERS=4
DEBUG=False
```

### Generate Secret Key

```bash
# Using Python
python -c "import secrets; print(secrets.token_hex(32))"

# Using OpenSSL
openssl rand -hex 32
```

## 📦 Docker Commands

### Build & Start

```bash
# Production
docker compose -f docker-compose.prod.yml up -d

# Development
docker compose -f docker-compose.dev.yml up -d

# Build without cache
docker compose -f docker-compose.prod.yml build --no-cache

# Start specific service
docker compose -f docker-compose.prod.yml up -d backend
```

### Stop & Remove

```bash
# Stop all services
docker compose -f docker-compose.prod.yml down

# Stop and remove volumes (⚠️ deletes data!)
docker compose -f docker-compose.prod.yml down -v

# Remove all containers, networks, and images
docker compose -f docker-compose.prod.yml down --rmi all
```

### Logs & Debugging

```bash
# View all logs
docker compose -f docker-compose.prod.yml logs

# Follow logs
docker compose -f docker-compose.prod.yml logs -f

# Logs for specific service
docker compose -f docker-compose.prod.yml logs -f backend

# Last 100 lines
docker compose -f docker-compose.prod.yml logs --tail=100
```

### Execute Commands in Container

```bash
# Run migrations
docker compose -f docker-compose.prod.yml exec backend alembic upgrade head

# Create migration
docker compose -f docker-compose.prod.yml exec backend alembic revision --autogenerate -m "Add new field"

# Open shell
docker compose -f docker-compose.prod.yml exec backend bash

# Run Python shell
docker compose -f docker-compose.prod.yml exec backend python

# Check database connection
docker compose -f docker-compose.prod.yml exec backend python -c "from database import engine; print(engine.connect())"
```

## 🔍 Health Checks

### Check Service Health

```bash
# Backend API
curl http://localhost:8000/health

# Or with Docker
docker compose -f docker-compose.prod.yml ps

# Detailed health status
docker inspect backend-api | grep -A 10 Health
```

## 🗄️ Database Management

### Backup Database

```bash
# Create backup
docker compose -f docker-compose.prod.yml exec postgres pg_dump -U postgres backend_db > backup.sql

# Backup with timestamp
docker compose -f docker-compose.prod.yml exec postgres pg_dump -U postgres backend_db > backup_$(date +%Y%m%d_%H%M%S).sql
```

### Restore Database

```bash
# Restore from backup
docker compose -f docker-compose.prod.yml exec -T postgres psql -U postgres backend_db < backup.sql
```

### Access PostgreSQL

```bash
# PostgreSQL shell
docker compose -f docker-compose.prod.yml exec postgres psql -U postgres -d backend_db

# Run SQL query
docker compose -f docker-compose.prod.yml exec postgres psql -U postgres -d backend_db -c "SELECT * FROM users;"
```

## 🔄 Updates & Maintenance

### Update Application

```bash
# 1. Pull latest changes
git pull

# 2. Rebuild images
docker compose -f docker-compose.prod.yml build

# 3. Restart services
docker compose -f docker-compose.prod.yml up -d

# 4. Run migrations
docker compose -f docker-compose.prod.yml exec backend alembic upgrade head
```

### Zero-Downtime Update

```bash
# 1. Scale up new instances
docker compose -f docker-compose.prod.yml up -d --scale backend=2

# 2. Wait for health check
sleep 10

# 3. Remove old containers
docker compose -f docker-compose.prod.yml up -d --scale backend=1
```

## 🚀 Production Best Practices

### 1. Use Secret Management

```bash
# Docker Secrets (Swarm)
echo "my_secret_key" | docker secret create secret_key -

# Or use environment variables from secure vault
# AWS Secrets Manager, HashiCorp Vault, etc.
```

### 2. Enable HTTPS

Add a reverse proxy (Nginx/Traefik):

```yaml
# Add to docker-compose.prod.yml
nginx:
  image: nginx:alpine
  ports:
    - "80:80"
    - "443:443"
  volumes:
    - ./nginx.conf:/etc/nginx/nginx.conf
    - ./ssl:/etc/nginx/ssl
```

### 3. Monitor Containers

```bash
# Resource usage
docker stats

# Specific container
docker stats backend-api

# All services
docker compose -f docker-compose.prod.yml stats
```

### 4. Set Resource Limits

Add to `docker-compose.prod.yml`:

```yaml
services:
  backend:
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
        reservations:
          cpus: '1.0'
          memory: 512M
```

## 🐛 Troubleshooting

### Container Won't Start

```bash
# Check logs
docker compose -f docker-compose.prod.yml logs backend

# Check container status
docker compose -f docker-compose.prod.yml ps

# Inspect container
docker inspect backend-api
```

### Database Connection Issues

```bash
# Test database connection
docker compose -f docker-compose.prod.yml exec postgres pg_isready

# Check if database exists
docker compose -f docker-compose.prod.yml exec postgres psql -U postgres -l
```

### Port Already in Use

```bash
# Find process using port
lsof -i :8000

# Or change port in .env
API_PORT=8001
```

### Reset Everything

```bash
# ⚠️ WARNING: This deletes all data!
docker compose -f docker-compose.prod.yml down -v
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml exec backend alembic upgrade head
```

## 📊 Monitoring & Logs

### Centralized Logging

Use Docker logging drivers:

```yaml
services:
  backend:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

### External Monitoring

- **Prometheus**: Metrics collection
- **Grafana**: Visualization
- **Sentry**: Error tracking
- **Datadog**: Full-stack monitoring

## 🔐 Security Checklist

- ✅ Use strong `SECRET_KEY`
- ✅ Set `DEBUG=False` in production
- ✅ Use HTTPS with valid certificates
- ✅ Restrict CORS origins
- ✅ Keep Docker images updated
- ✅ Use non-root user in containers
- ✅ Scan images for vulnerabilities
- ✅ Use secrets management
- ✅ Enable firewall rules
- ✅ Regular backups

## 📚 Additional Resources

- [Docker Documentation](https://docs.docker.com/)
- [Docker Compose File Reference](https://docs.docker.com/compose/compose-file/)
- [FastAPI Deployment](https://fastapi.tiangolo.com/deployment/)
- [PostgreSQL Docker](https://hub.docker.com/_/postgres)
