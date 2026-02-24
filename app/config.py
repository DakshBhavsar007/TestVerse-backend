from pydantic_settings import BaseSettings
from pydantic import ConfigDict
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )
    # MongoDB
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db_name: str = "testverse"
    # App
    app_secret_key: str = "change_me_in_production"
    environment: str = "development"
    app_url: str = "http://localhost:5173"   # Frontend URL for email links
    google_gemini_api_key: Optional[str] = None
    # Groq (free AI API)
    groq_api_key: Optional[str] = None
    # Crawler
    max_crawl_pages: int = 50
    crawl_timeout_seconds: int = 30
    request_timeout_seconds: int = 15
    # Reports
    reports_dir: str = "reports"
    # Playwright
    playwright_workers: int = 3
    # Rate limiting
    rate_limit_per_minute: int = 10
    # Credential encryption (Fernet key)
    # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    credential_encryption_key: str = ""
    # SendGrid email
    sendgrid_api_key: Optional[str] = None
    sendgrid_from_email: str = "noreply@testverse.app"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
