"""Модуль правил валидации, классификации и сопоставления категорий по площадкам."""

from typing import Dict, Any, List


ROOT_CATEGORIES = ["Букеты", "Свадебные", "Композиции", "Декор", "Доп. товары"]

TILDA_BOUQUET_CATEGORIES = {
    "Нежный": "nezhnij",
    "В стиле REN": "ren-style",
    "Розы": "roses",
    "Премиум": "premium",
    "Сезонные": "seasonal",
    "Свадебные": "wedding"
}


def get_flowwow_subcategory(root_cat: str, is_mono: bool, sub_type: str = "") -> int:
    """
    Определение subCategoryId для Flowwow:
      1: Монобукеты
      2: Букеты сборные / Свадебные
      3: Цветы в коробке
      4: Цветы в корзине
      5: Композиции
    """
    if root_cat == "Букеты":
        return 1 if is_mono else 2
    elif root_cat == "Свадебные":
        return 2  # Свадебные букеты регистрируются в букетах с соответствующими тегами
    elif root_cat == "Композиции":
        if "коробк" in sub_type.lower():
            return 3
        elif "корзин" in sub_type.lower():
            return 4
        return 5
    elif root_cat == "Декор":
        return 7
    return 2


def get_okpd2_code(flower_name_or_mix: str) -> Dict[str, str]:
    """Определение кода ОКПД2 и наименования для Яндекс Еды."""
    name_lower = flower_name_or_mix.lower()

    if "роз" in name_lower:
        return {"code": "01.19.21.110", "name": "Розы срезанные"}
    elif "гвоздик" in name_lower or "диантус" in name_lower:
        return {"code": "01.19.21.120", "name": "Гвоздики срезанные"}
    elif "орхиде" in name_lower:
        return {"code": "01.19.21.130", "name": "Орхидеи срезанные"}
    elif "гладиолус" in name_lower:
        return {"code": "01.19.21.140", "name": "Гладиолусы срезанные"}
    elif "хризантем" in name_lower:
        return {"code": "01.19.21.150", "name": "Хризантемы срезанные"}
    else:
        return {"code": "01.19.21.190", "name": "Цветы срезанные прочие / Букеты сборные"}


def calculate_margin(selling_price: float, cost_price: float) -> float:
    """Расчет маржи в процентах."""
    if selling_price <= 0:
        return 0.0
    margin = ((selling_price - cost_price) / selling_price) * 100.0
    return round(margin, 1)
