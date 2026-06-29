# Backend

[![Coverage](https://img.shields.io/endpoint?url=https://six7-click-n-deploy.github.io/backend/badge.json)](https://six7-click-n-deploy.github.io/backend/)

FastAPI-Backend des App Stores. Nimmt REST-Anfragen vom Frontend entgegen, validiert Keycloak-Tokens, persistiert in PostgreSQL und dispatcht Deployment-Tasks an den Celery-Worker via RabbitMQ.

## Setup

Dieses Repository wird nicht eigenständig gestartet. Der gesamte Stack — inklusive Backend — wird über das deployment-Repository hochgefahren. Vollständige Anleitung: [deployment/README.md](https://github.com/six7-click-n-deploy/deployment#readme).

Voraussetzung für alle folgenden Befehle: `make dev-up` aus dem `deployment/`-Verzeichnis wurde ausgeführt und der Stack läuft.

## Entwicklung

Alle `make`-Befehle werden aus dem `deployment/`-Verzeichnis des [deployment-Repos](https://github.com/six7-click-n-deploy/deployment) ausgeführt — dort liegt das Makefile.

```bash
# in app-store/deployment
make test-backend       # pytest im Backend-Container
make lint-backend       # ruff check
make lint-backend-fix   # ruff check --fix
make format-backend     # ruff format
make shell-backend      # interaktive Shell im Container
```

## Datenbank-Migrationen

Ebenfalls aus `deployment/`:

```bash
# in app-store/deployment
make migrate-dev                              # Schema auf head bringen
make migration-create MSG="add foo column"    # Autogenerate aus Models
make migration-history                        # Migrations-Historie
make migration-current                        # aktuelle Revision
make migration-downgrade                      # eine Revision zurück
```

## API-Dokumentation

Swagger-UI mit allen Endpoints: http://localhost:8000/docs (nach `make dev-up`).

## Technologie-Stack

- **FastAPI** + **Uvicorn** (ASGI, 4 Worker)
- **SQLAlchemy 2.0** ORM, **Alembic** für Migrationen
- **Pydantic** für Request-/Response-Validierung
- **python-keycloak** für OIDC-Token-Validierung
- **Celery** als Producer (Tasks gehen an Worker via RabbitMQ)
- **Ruff** für Linting und Formatting
- **pytest** mit `unit`/`integration`/`api`-Markern

## Mehr

- Architektur und projektübergreifende Doku: [.github-Repo](https://github.com/six7-click-n-deploy/.github)
- Worker-Service: [worker-Repo](https://github.com/six7-click-n-deploy/worker)
