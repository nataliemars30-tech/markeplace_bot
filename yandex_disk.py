"""Интеграция с Яндекс Диском для сохранения фото и видео высокого качества."""

import logging
from pathlib import Path
from typing import Dict, Any, Optional
import httpx
from config import config

logger = logging.getLogger(__name__)


class YandexDiskError(Exception):
    pass


class YandexDiskClient:
    BASE_URL = "https://cloud-api.yandex.net/v1/disk/resources"

    def __init__(self, token: Optional[str] = None):
        self.token = token or config.yandex_disk_token

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"OAuth {self.token}",
            "Accept": "application/json"
        }

    async def ensure_folder(self, disk_path: str) -> None:
        """Создание директории на Яндекс Диске (с поддержкой вложенности)."""
        clean_path = disk_path.strip("/")
        parts = clean_path.split("/")
        current = ""
        async with httpx.AsyncClient(timeout=15.0) as client:
            for part in parts:
                current += f"/{part}"
                params = {"path": current}
                resp = await client.put(self.BASE_URL, headers=self._get_headers(), params=params)
                # 201 Created или 409 Conflict (уже существует) — норма
                if resp.status_code not in (201, 409):
                    logger.warning(f"Ошибка создания папки {current}: {resp.status_code} {resp.text}")

    async def upload_file(self, local_path: Path, disk_path: str) -> str:
        """
        Загрузка локального файла на Яндекс Диск, публикация ресурса
        и возврат прямой публичной ссылки на файл.
        """
        async with httpx.AsyncClient(timeout=60.0) as client:
            # 1. Запрос URL для загрузки
            params = {"path": disk_path, "overwrite": "true"}
            upload_link_resp = await client.get(
                f"{self.BASE_URL}/upload",
                headers=self._get_headers(),
                params=params
            )
            if upload_link_resp.status_code != 200:
                raise YandexDiskError(f"Не удалось получить ссылку для загрузки: {upload_link_resp.text}")

            upload_url = upload_link_resp.json().get("href")

            # 2. Передача бинарных данных файла (AsyncClient требует bytes, не file-object)
            file_data = Path(local_path).read_bytes()
            upload_resp = await client.put(upload_url, content=file_data)
            if upload_resp.status_code not in (200, 201, 202):
                raise YandexDiskError(f"Ошибка загрузки файла на Яндекс Диск: {upload_resp.status_code}")

            # 3. Публикация файла
            pub_resp = await client.put(
                f"{self.BASE_URL}/publish",
                headers=self._get_headers(),
                params={"path": disk_path}
            )
            if pub_resp.status_code not in (200, 409):
                logger.warning(f"Предупреждение при публикации {disk_path}: {pub_resp.text}")

            # 4. Получение публичной ссылки
            meta_resp = await client.get(
                self.BASE_URL,
                headers=self._get_headers(),
                params={"path": disk_path}
            )
            if meta_resp.status_code == 200:
                meta = meta_resp.json()
                public_url = meta.get("public_url")
                # Для прямого скачивания/вставки в карточки
                file_url = meta.get("file") or public_url
                return file_url or public_url

            raise YandexDiskError(f"Не удалось получить метаданные опубликованного файла: {meta_resp.text}")
