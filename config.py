"""Конфигурация проекта REN Flowwow & Marketplace Agent.
Загрузка параметров из файла .env с валидацией через Pydantic.
"""

import os
from pathlib import Path
from typing import Set
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Telegram
    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    telegram_allowed_ids: str = Field(..., alias="TELEGRAM_ALLOWED_IDS")

    # Flowwow
    flowwow_token: str = Field(..., alias="FLOWWOW_TOKEN")
    flowwow_shop_id: int = Field(222976, alias="FLOWWOW_SHOP_ID")

    # МойСклад
    moysklad_token: str = Field(..., alias="MOYSKLAD_TOKEN")

    # Яндекс Маркет
    yandex_market_api_key: str = Field("", alias="YANDEX_MARKET_API_KEY")

    # Tilda MCP (Профиль -> Ключи MCP)
    tilda_mcp_token: str = Field("", alias="TILDA_MCP_TOKEN")
    tilda_project_id: int = Field(13756635, alias="TILDA_PROJECT_ID")

    # LLM для черновиков ответов на отзывы (OpenAI-совместимый, опционально)
    openai_api_key: str = Field("", alias="OPENAI_API_KEY")
    openai_base_url: str = Field("https://api.moonshot.cn/v1", alias="OPENAI_BASE_URL")
    openai_model: str = Field("moonshot-v1-8k", alias="OPENAI_MODEL")

    # Поллер отзывов Flowwow (on/off) — по умолчанию выключен
    reviews_enabled: str = Field("off", alias="REVIEWS_ENABLED")

    # Яндекс Диск
    yandex_disk_token: str = Field(..., alias="YANDEX_DISK_TOKEN")

    # Google Sheets
    google_service_account_file: str = Field("google_credentials.json", alias="GOOGLE_SERVICE_ACCOUNT_FILE")
    google_spreadsheet_key: str = Field("", alias="GOOGLE_SPREADSHEET_KEY")

    # Внутренние директории
    base_dir: Path = Path(__file__).resolve().parent
    temp_dir: Path = Path(__file__).resolve().parent / "data" / "temp"
    db_path: Path = Path(__file__).resolve().parent / "data" / "products.db"

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(__file__), ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def allowed_tg_ids(self) -> Set[int]:
        """Парсинг разрешенных Telegram ID."""
        ids = set()
        for raw_id in self.telegram_allowed_ids.split(","):
            raw_id = raw_id.strip()
            if raw_id.isdigit():
                ids.add(int(raw_id))
        return ids


config = Settings()
