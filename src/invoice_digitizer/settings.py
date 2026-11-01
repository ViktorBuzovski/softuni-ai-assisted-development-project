from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    max_upload_bytes: int = 10 * 1024 * 1024
    # Placeholder only: this proof of concept runs on a Claude Code subscription.
    anthropic_api_key: str = "dummy-key"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "invoices.db"
