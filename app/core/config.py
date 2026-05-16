from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str
    openai_api_key: str
    database_url: str
    default_timezone: str = "Europe/Istanbul"

    twelve_data_api_key: str | None = None
    eodhd_api_key: str | None = None


settings = Settings()
