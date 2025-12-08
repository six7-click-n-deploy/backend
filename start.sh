#!/bin/bash
# ================================================================
# Production Startup Script
# Runs database migrations and starts the FastAPI application
# ================================================================

set -e  # Exit on error

echo "🚀 Starting Backend API..."

# ----------------------------------------------------------------
# Wait for Database
# ----------------------------------------------------------------
echo "⏳ Waiting for database connection..."
max_retries=30
counter=0

until python -c "from app.database import engine; engine.connect()" 2>/dev/null; do
    counter=$((counter+1))
    if [ $counter -gt $max_retries ]; then
        echo "❌ Failed to connect to database after $max_retries attempts"
        exit 1
    fi
    echo "   Attempt $counter/$max_retries - Database not ready yet..."
    sleep 2
done

echo "✓ Database connection established"

# ----------------------------------------------------------------
# Run Migrations
# ----------------------------------------------------------------
echo "🗄️  Running database migrations..."
alembic upgrade head
echo "✓ Migrations completed"

# ----------------------------------------------------------------
# Start Application
# ----------------------------------------------------------------
echo "🌐 Starting FastAPI application..."
echo "   Host: ${HOST:-0.0.0.0}"
echo "   Port: ${PORT:-8000}"
echo "   Workers: ${WORKERS:-4}"
echo "   Debug: ${DEBUG:-False}"

exec uvicorn app.main:app \
    --host "${HOST:-0.0.0.0}" \
    --port "${PORT:-8000}" \
    --workers "${WORKERS:-4}" \
    --log-level "${LOG_LEVEL:-info}" \
    --proxy-headers \
    --forwarded-allow-ips='*'
