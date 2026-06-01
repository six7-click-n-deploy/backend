
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Backend API"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str

    # Celery (optional - only needed for API runtime, not for migrations)
    CELERY_BROKER_URL: str = "amqp://admin:admin@rabbitmq:5672/"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/0"

    # Git
    TEMP_REPO_BASE_PATH: str = "/tmp/worker_repos"
    GIT_ACCESS_TOKEN: str = ""  # Token for HTTPS git authentication

    # Keycloak — single source of truth for authentication
    KEYCLOAK_SERVER_URL: str = "http://keycloak:8080"
    KEYCLOAK_REALM: str = "dhbw"
    KEYCLOAK_CLIENT_ID: str = "appstore-backend"
    KEYCLOAK_CLIENT_SECRET: str = ""  # Set via environment variable

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # Symmetric Fernet key shared with the worker. Used to encrypt OpenStack
    # credentials at rest and to seal the envelope shipped through Celery.
    # Generate: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
    CREDENTIAL_ENCRYPTION_KEY: str

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()
