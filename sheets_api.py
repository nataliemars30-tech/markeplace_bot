"""Интеграция с Google Sheets для ведения раздельных листов каталога REN."""

import json
import logging
import os
from datetime import datetime
from typing import Dict, Any
import gspread
from google.oauth2.service_account import Credentials
from config import config

logger = logging.getLogger(__name__)

HEADERS_MAP = {
    "Букеты": [
        "Артикул", "Название", "Размер/Штук", "Flowwow ID", "Ссылка Flowwow", "Базовая цена",
        "Цена Flowwow", "Цена Еда (+30%)", "Цена Маркет (+50%)", "Цена Tilda/Карты (+10%)",
        "Себестоимость", "Маржа %", "Заказ МойСклад",
        "Категория Tilda", "ОКПД2 Код", "ОКПД2 Название", "ТН ВЭД",
        "Состав букета", "Ширина", "Высота",
        "Статус Flowwow", "Статус Маркет", "Статус Еда", "Статус Tilda",
        "Обложка (URL)", "Все фото (JSON)", "Папка Яндекс Диск",
        "Дата создания", "Создатель (TG ID)"
    ],
    "Свадебные": [
        "Артикул", "Название", "Размер", "Flowwow ID", "Ссылка Flowwow", "Базовая цена",
        "Цена Flowwow", "Цена Еда (+30%)", "Цена Маркет (+50%)", "Цена Tilda/Карты (+10%)",
        "Себестоимость", "Маржа %", "Заказ МойСклад",
        "Категория Tilda", "ОКПД2 Код", "ОКПД2 Название", "ТН ВЭД",
        "Состав букета", "Ширина", "Высота",
        "Статус Flowwow", "Статус Маркет", "Статус Еда", "Статус Tilda",
        "Обложка (URL)", "Все фото (JSON)", "Папка Яндекс Диск",
        "Дата создания", "Создатель (TG ID)"
    ],
    "Композиции": [
        "Артикул", "Название", "Тип (Коробка/Корзина)", "Flowwow ID", "Цена", "Себестоимость",
        "Маржа %", "Заказ МойСклад", "ОКПД2 Код", "Обложка (URL)", "Папка Яндекс Диск", "Дата"
    ],
    "Декор": [
        "Артикул", "Название", "Материал", "Flowwow ID", "Цена", "Себестоимость",
        "Маржа %", "Остаток", "Обложка (URL)", "Дата"
    ],
    "Доп. товары": [
        "Артикул", "Название", "Тип (Свеча/Открытка)", "Цена", "Себестоимость",
        "Маржа %", "Остаток", "Дата"
    ]
}


class GoogleSheetsManager:
    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    def __init__(self):
        self.creds_file = config.base_dir / config.google_service_account_file
        self.spreadsheet_key = config.google_spreadsheet_key
        self._client: gspread.Client | None = None

    def _get_client(self) -> gspread.Client:
        if self._client is None:
            # Приоритет 1: ключ из переменной окружения (Railway) — JSON одной строкой
            creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip()
            if creds_json:
                creds = Credentials.from_service_account_info(
                    json.loads(creds_json), scopes=self.SCOPES
                )
                self._client = gspread.authorize(creds)
                return self._client
            # Приоритет 2: локальный файл (разработка)
            if not self.creds_file.exists():
                raise FileNotFoundError(
                    f"Google-ключ не найден: ни переменная GOOGLE_CREDENTIALS_JSON, "
                    f"ни файл {self.creds_file}"
                )
            creds = Credentials.from_service_account_file(str(self.creds_file), scopes=self.SCOPES)
            self._client = gspread.authorize(creds)
        return self._client

    def append_row(self, sheet_name: str, item_data: Dict[str, Any]) -> bool:
        """Добавление строки в соответствующий лист Google-таблицы."""
        try:
            client = self._get_client()
            spreadsheet = client.open_by_key(self.spreadsheet_key) if self.spreadsheet_key else client.open("Каталог REN")

            headers = HEADERS_MAP.get(sheet_name, HEADERS_MAP["Букеты"])

            try:
                worksheet = spreadsheet.worksheet(sheet_name)
            except gspread.WorksheetNotFound:
                worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=len(headers))
                worksheet.append_row(headers)

            # Починка заголовка, если лист создан старой версией бота
            current_header = worksheet.row_values(1)
            if current_header and current_header != headers:
                logger.warning(f"Лист {sheet_name}: заголовок устарел — обновляю ({len(current_header)} -> {len(headers)} колонок)")
                for i, h in enumerate(headers, 1):
                    worksheet.update_cell(1, i, h)

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if sheet_name in ("Букеты", "Свадебные"):
                row = [
                    item_data.get("full_article", ""),
                    item_data.get("name", ""),
                    item_data.get("variation", ""),
                    str(item_data.get("flowwow_id", "")),
                    item_data.get("flowwow_url", ""),
                    item_data.get("price", 0),
                    item_data.get("flowwow_price", ""),
                    item_data.get("eda_price", ""),
                    item_data.get("market_price", ""),
                    item_data.get("tilda_price", ""),
                    item_data.get("cost_price", 0),
                    f"{item_data.get('margin_percent', 0)}%",
                    str(item_data.get("moysklad_order_no", "")),
                    item_data.get("tilda_category", ""),
                    item_data.get("okpd2_code", ""),
                    item_data.get("okpd2_name", ""),
                    item_data.get("tnved", ""),
                    item_data.get("consist", ""),
                    item_data.get("width", ""),
                    item_data.get("height", ""),
                    item_data.get("status_flowwow", ""),
                    item_data.get("status_market", ""),
                    item_data.get("status_eda", ""),
                    item_data.get("status_tilda", ""),
                    item_data.get("cover_url", ""),
                    item_data.get("all_photos", ""),
                    item_data.get("disk_folder_url", ""),
                    now_str,
                    str(item_data.get("created_by_tg_id", ""))
                ]
            else:
                row = [
                    item_data.get("full_article", ""),
                    item_data.get("name", ""),
                    item_data.get("variation", ""),
                    str(item_data.get("flowwow_id", "")),
                    item_data.get("price", 0),
                    item_data.get("cost_price", 0),
                    f"{item_data.get('margin_percent', 0)}%",
                    item_data.get("cover_url", ""),
                    now_str
                ]

            worksheet.append_row(row)
            return True
        except Exception as e:
            logger.error(f"Ошибка записи в Google Sheets [{sheet_name}]: {e}")
            return False
