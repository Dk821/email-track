from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # SMTP
    smtp_host: str = "mail.privateemail.com"
    smtp_port: int = 587
    smtp_email: str
    smtp_password: str
    smtp_from_name: str = "Email Tracker"
    smtp_use_tls: bool = False
    smtp_start_tls: bool = True

    # Database
    database_url: str = "sqlite:///email_tracker.db"

    # Application
    app_url: str = "http://localhost:8000"
    company_name: str = "Email Tracker"
    company_logo_url: str = ""
    max_body_size: int = 10 * 1024 * 1024  # 10 MB
    max_subject_length: int = 998

    # Environment
    debug: bool = Field(default=True, validation_alias="APP_DEBUG")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
