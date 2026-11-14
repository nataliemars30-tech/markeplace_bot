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
    "Название категории 1 уровень",
    "id категории 1 уровень\n(следует заполнять, если категория уже существует)",
    "Название категории 2 уровень",
    "id категории 2 уровень\n(следует заполнять, если категория уже существует)",
    "Название категории 3 уровень",
    "id категории 3 уровень\n(следует заполнять, если категория уже существует)",
    "Название позиции",
    "id позиции",
    "Общий вес позиции",
    "Единица измерения",
    "Квантовка\n(указывается, только если позиция весовая)",
    "Цена(обязательно)",
    "Описание товара",
    "Производитель",
    "Страна",
    "Штрихкоды\n(можно указывать через запятую)",
    "Товар доступен в магазинах\n(указываются place id)\nЕсли не указать ни один магазин, то позиция будет доступна для магазинов всей сети",
    "Акциза",
    "Ссылки на картинки\n(можно указывать через запятую)",
    "Налоговые коды товаров (для Узбекистана)",
    "Налоговые коды упаковок (для Узбекистана)",
    "Необходима маркировка (для Узбекистана)",
    "Товар для взрослых",
    "Кол-во товаров в упаковке",
    "Ширина (см)",
    "Высота (см)",
    "Состав букета (в формате: Наименование позиции:кол-во,Наименование позиции:кол-во,...и т.д.. Без пробелов. Например Роза пионовидная:10,Ромашка:5,... и т.д..) Наименование позиции нужно брать из справочника"
]

SIZE_DIMS = {"S": (25, 35, 600), "M": (35, 45, 900), "L": (45, 55, 1400)}


def _plural_gen(word: str) -> str:
    """'роза' -> 'роз', 'тюльпан' -> 'тюльпанов', 'гвоздика' -> 'гвоздик'."""
    w = word.lower().strip()
    if w.endswith(("ая", "яя")):      # прилагательное: 'красные' -> 'красных'
        return w[:-2] + "ых"
    if w.endswith("а"):
        return w[:-1]
    if w.endswith("я"):
        return w[:-1] + "й"
    if w.endswith(("ь", "й")):
        return w[:-1] + "ей"
    return w + "ов"


def build_eda_row(*, name: str, variation_label: str, is_mono: bool,
                  price_eda: int, positions: List[Dict[str, Any]],
                  picture_urls: List[str], description: str,
                  height_cm: int = 0, width_cm: int = 0) -> List[str]:
    """Одна строка CSV под формат импорта ЛК.
    Вес ВСЕГДА 1 кг; габариты — от флориста, иначе по размеру."""
    if is_mono and positions:
        cat_name = "Монобукеты"
        qty = sum(int(p.get("qty", 1)) for p in positions)
        flower = _plural_gen(str(positions[0]["name"]).split()[0])
        title = f"Букет из {qty} {flower}"[:100]
    elif is_mono:
        cat_name = "Монобукеты"
        qty = int(variation_label) if str(variation_label).isdigit() else 1
        title = f"Букет из {qty} {name}"[:100]
    else:
        cat_name = "Авторские букеты"
        title = f"Авторский букет «{name}» {variation_label}"[:100]
    dims_w, dims_h, _ = SIZE_DIMS.get(str(variation_label).upper(), (35, 45, 1000))
    width = width_cm or (25 if is_mono else dims_w)
    height = height_cm or dims_h
    weight = 1000  # всегда 1 кг (правило владельца)

    consist_str = ",".join(f"{p['name']}:{int(p['qty'])}" for p in positions)
    if not consist_str:
        # Состав обязателен для сохранения карточки в ЛК Еды — не оставляем пустым
        consist_str = f"{title}:1"

    return [
        cat_name, CAT_IDS[cat_name],          # 1-2: категория + id
        "", "", "", "",                        # 3-6: подкатегории
        title, "",                             # 7-8: название, id позиции (новая)
        str(weight), "г", "",                  # 9-11: вес
        str(price_eda),                        # 12: цена
        description[:900],                     # 13: описание
        "REN", "Россия",                       # 14-15
        "", PLACES, "",                        # 16-18: штрихкоды, магазины, акциза
        ",".join(picture_urls[:10]),           # 19: все фото через запятую
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
            existing: List[List[str]] = []
            if self.csv_path.exists():
                with open(self.csv_path, encoding="utf-8-sig", newline="") as f:
                    existing = list(csv.reader(f, delimiter=";"))
            # Починка заголовка, если файл создан старой версией бота
            if existing and existing[0] != HEADERS_27:
                logger.warning("CSV Еды: заголовок не совпадает с форматом ЛК — перезаписываю")
                existing = [HEADERS_27] + [r for r in existing[1:] if r]
            elif not existing:
                existing = [HEADERS_27]
            existing.append(row)
            with open(self.csv_path, "w", encoding="utf-8-sig", newline="") as f:
                csv.writer(f, delimiter=";").writerows(existing)
            return True
        except Exception as e:
            logger.error(f"Ошибка записи в CSV Еды: {e}")
            return False
