from pydantic_settings import BaseSettings
from typing import List
import os

class Settings(BaseSettings):
    # App
    APP_NAME: str = "Backend API"
    DEBUG: bool = False
    
    # Database
    DATABASE_URL: str
    
    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # Celery
    CELERY_BROKER_URL: str
    CELERY_RESULT_BACKEND: str
    
    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:5173"]
    
    # Google OAuth (optional)
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    
    # OpenStack (optional)
    OS_AUTH_URL: str = ""
    OS_PROJECT_ID: str = ""
    OS_PROJECT_NAME: str = ""
    OS_USER_DOMAIN_NAME: str = "Default"
    OS_USERNAME: str = ""
    OS_PASSWORD: str = ""
    OS_REGION_NAME: str = ""
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()