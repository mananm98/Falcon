from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    app_name: str = "Falcon"
    app_version: str = "0.1.0"
    debug: bool = False

    # Storage
    wiki_storage_root: Path = Path(__file__).resolve().parent.parent.parent / "wiki_storage"
    database_path: Path = Path(__file__).resolve().parent.parent / "falcon.db"

    # Codex CLI
    codex_api_key: str = ""
    codex_timeout_seconds: int = 300
    codex_max_concurrent: int = 3

    # Job queue
    max_concurrent_jobs: int = 2
    job_max_attempts: int = 3
    job_poll_interval_seconds: float = 1.0

    # Sandbox
    use_daytona: bool = False
    daytona_api_key: str = ""
    daytona_api_url: str = "https://app.daytona.io/api"

    # GitHub
    github_api_token: str = ""

    model_config = {"env_prefix": "FALCON_", "env_file": ".env"}


settings = Settings()
