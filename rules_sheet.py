"""Правила из Google-таблицы — единый источник постоянных настроек.

Лист «Правила» (создаётся автоматически) с колонками: Раздел | Ключ | Значение.
Владелец может менять значения прямо в таблице — бот подхватит при следующем старте
(или командой /reload). Если таблица недоступна — используются значения по умолчанию.
"""

import json
import logging
from typing import Any, Dict

from sheets_api import GoogleSheetsManager

logger = logging.getLogger(__name__)

SHEET_NAME = "Правила"
HEADERS = ["Раздел", "Ключ", "Значение"]

DEFAULTS: Dict[str, Dict[str, str]] = {
    "flowwow": {
        "manufacturer_name": "REN, Россия",
        "manufacturer_address": "Россия, Санкт-Петербург",
    },
    "market": {
        "bouquet_width_cm": "25",
    },
    "tilda": {
        "options_title": "Дополнительно",
        "options": json.dumps([
            {"value": "Открытка фирменная", "price": "150"},
            {"value": "Транспортировочный набор (коробка и аквабокс)", "price": "490"},
            {"value": "Секатор", "price": "900"},
        ], ensure_ascii=False),
    },
    "тексты": {
        "стандартный_блок_описания": (
            "Доставляется по Санкт-Петербургу в транспортировочном аквабоксе с водой — "
            "цветы приезжают такими же свежими, какими покинули студию 💐"
        ),
    },
}

_rules: Dict[str, Dict[str, str]] | None = None


def _seed(manager: GoogleSheetsManager, ws) -> None:
    for section, kv in DEFAULTS.items():
        for key, value in kv.items():
            ws.append_row([section, key, value])


def load_rules(force: bool = False) -> Dict[str, Dict[str, str]]:
    """Загрузка правил из листа «Правила». При сбое — DEFAULTS."""
    global _rules
    if _rules is not None and not force:
        return _rules
    rules = {s: dict(kv) for s, kv in DEFAULTS.items()}
    try:
        manager = GoogleSheetsManager()
        client = manager._get_client()
        spreadsheet = client.open_by_key(manager.spreadsheet_key) if manager.spreadsheet_key \
            else client.open("Каталог REN")
        try:
            ws = spreadsheet.worksheet(SHEET_NAME)
        except Exception:
            ws = spreadsheet.add_worksheet(title=SHEET_NAME, rows=200, cols=len(HEADERS))
            ws.append_row(HEADERS)
            _seed(manager, ws)
            logger.info("Лист «Правила» создан и заполнен значениями по умолчанию")
        values = ws.get_all_values()
        for row in values[1:]:
            if len(row) < 3 or not row[0].strip():
                continue
            rules.setdefault(row[0].strip(), {})[row[1].strip()] = row[2].strip()
        logger.info(f"Правила загружены из таблицы: {sum(len(v) for v in rules.values())} значений")
    except Exception as e:
        logger.warning(f"Лист «Правила» недоступен ({e}) — использую значения по умолчанию")
    _rules = rules
    return rules


def get(section: str, key: str, default: str = "") -> str:
    return load_rules().get(section, {}).get(key, default)


def reload_rules() -> Dict[str, Dict[str, str]]:
    return load_rules(force=True)
