
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

    # SMTP (Gmail). Required for the post-deploy notification mails.
    # Generate an "App password" in Google account settings (the regular
    # password won't work with 2FA enabled).
    #
    # SMTP_ENABLED is the explicit kill-switch — set it to False to turn
    # mail delivery into a no-op even when SMTP_USER/SMTP_PASSWORD are
    # populated. Two reasons it lives separately from the credentials:
    # (1) operators routinely keep the Gmail app-password in .env for
    # later use but want mail off in dev / CI / on demo days, and
    # (2) the resend-access endpoint needs to distinguish "we chose
    # not to send" (HTTP 503, configuration intent) from "we tried and
    # SMTP refused" (HTTP 502, infrastructure problem) — that
    # distinction is impossible if "off" and "creds missing" share a
    # state.
    SMTP_ENABLED: bool = False
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""
    SMTP_FROM_NAME: str = "Click-n-Deploy"

    # Public URL the deployment detail page is reachable under, used in
    # the owner-summary mail to deep-link back into the UI. No trailing
    # slash. Falls back to the first CORS origin in dev.
    APP_BASE_URL: str = "http://localhost:5173"

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()
