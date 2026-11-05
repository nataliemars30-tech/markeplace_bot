"""Генератор CSV для импорта каталога Яндекс Еда (Ритейл).

Формат: 27 колонок по факту выгрузки ЛК (проверено 05.11.2026).
ВАЖНО: юридические поля (ОКПД2, оценка соответствия, госрегистрация) в CSV
не проходят — их проставляем в редакторе карточки отдельным ручным шагом.
Файл живёт на Volume (/app/data) и отдаётся ботом по маршруту /eda.csv.
"""

import csv
import logging
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

# Категории ЛК REN (id из выгрузки)
CAT_IDS = {
    "Монобукеты": "6984f6e6-9562-452c-a822-9898dafce66d",
    "Авторские букеты": "5136113f-547b-4f48-9f7d-907102f9ddef",
    "Цветы поштучно": "4ff50210-e19a-4b76-9ae8-2efe202e7bf5",
}
PLACES = "3282482,3282484"  # оба магазина (place id из ЛК)

HEADERS_27 = [
    "Название категории 1 уровень", "id категории 1 уровень",
    "Название категории 2 уровень", "id категории 2 уровень",
    "Название категории 3 уровень", "id категории 3 уровень",
    "Название позиции", "id позиции",
    "Общий вес позиции", "Единица измерения", "Квантовка",
    "Цена(обязательно)", "Описание товара", "Производитель", "Страна",
    "Штрихкоды", "Товар доступен в магазинах", "Акциза",
    "Ссылки на картинки", "Налоговые коды товаров (для Узбекистана)",
    "Налоговые коды упаковок (для Узбекистана)", "Необходима маркировка (для Узбекистана)",
    "Товар для взрослых", "Кол-во товаров в упаковке",
    "Ширина (см)", "Высота (см)", "Состав букета"
]

SIZE_DIMS = {"S": (25, 35, 600), "M": (35, 45, 900), "L": (45, 55, 1400)}


def build_eda_row(*, name: str, variation_label: str, is_mono: bool,
                  price_eda: int, positions: List[Dict[str, Any]],
                  cover_url: str, description: str) -> List[str]:
    """Одна строка CSV под формат импорта ЛК."""
    if is_mono:
        cat_name = "Монобукеты"
        qty = int(variation_label) if variation_label.isdigit() else 1
        title = f"Букет из {qty} {name}"[:100]
        width, height, weight = 30, 40, max(300, qty * 90)
    else:
        cat_name = "Авторские букеты"
        title = f"Авторский букет «{name}» {variation_label}"[:100]
        width, height, weight = SIZE_DIMS.get(variation_label.upper(), (35, 45, 900))

    consist_str = ",".join(f"{p['name']}:{int(p['qty'])}" for p in positions)

    return [
        cat_name, CAT_IDS[cat_name],          # 1-2: категория + id
        "", "", "", "",                        # 3-6: подкатегории
        title, "",                             # 7-8: название, id позиции (новая)
        str(weight), "г", "",                  # 9-11: вес
        str(price_eda),                        # 12: цена
        description[:900],                     # 13: описание
        "REN", "Россия",                       # 14-15
        "", PLACES, "",                        # 16-18: штрихкоды, магазины, акциза
        cover_url,                             # 19: фото
        "", "", "",                            # 20-22: Узбекистан
        "", "1",                               # 23-24: 18+, кол-во в упаковке
        str(width), str(height),               # 25-26: габариты
        consist_str                            # 27: состав
    ]


class EdaCsvManager:
    def __init__(self, csv_path: Path):
        self.csv_path = csv_path

    def append_product(self, row: List[str]) -> bool:
        try:
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            write_header = not self.csv_path.exists()
            with open(self.csv_path, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                if write_header:
                    w.writerow(HEADERS_27)
                w.writerow(row)
            return True
        except Exception as e:
            logger.error(f"Ошибка записи в CSV Еды: {e}")
            return False
