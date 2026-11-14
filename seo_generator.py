"""SEO-генератор описаний букетов по ТЗ 07.11.2026.

Если задан OPENAI_API_KEY (OpenAI-совместимый API: Kimi/Moonshot/OpenAI) —
описания пишет LLM по строгому фирменному промпту. Иначе — качественный шаблон.
Оба варианта: БЕЗ открыток (запрещено ТЗ), уход точечно под состав.
"""

import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from config import config
from platform_rules import care_tips
from rules_sheet import get as rule_get

logger = logging.getLogger(__name__)


def _standard_block() -> str:
    """Постоянный финальный блок описания — единый для всех букетов (лист «Правила»)."""
    return rule_get("тексты", "стандартный_блок_описания",
                    "Доставляется по Санкт-Петербургу в фирменной упаковке.")

PROMPT = """Ты — профессиональный флористический копирайтер и SEO-специалист цветочной студии REN в Санкт-Петербурге.

ВХОДНЫЕ ДАННЫЕ:
- Название букета: {bouquet_name}
- Цветовая палитра: {color_mood}
- Очищенный состав: {cleaned_composition} (перечень и количество)
- Оформление: {packaging_type} («Пленка» или «Под ленту»)

СТРОГИЕ ЗАПРЕТЫ:
1. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО упоминать открытки, записки, бесплатные подарки, слова «бесплатно», «скидка».
2. ЗАПРЕЩЁН банальный уход «подрежьте под 45 градусов» без учёта состава — уход ТОЧЕЧНЫЙ:
   гортензия -> высокий уровень прохладной воды (до шапки) и опрыскивание лепестков;
   тюльпаны -> ледяная вода, растут в вазе;
   розы -> глубокая ваза, удалить листья ниже уровня воды;
   хризантемы/гвоздики/диантусы -> стойкость, умеренная вода комнатной температуры.
3. Без канцелярита и шаблонных клише.

СТРУКТУРА (450–700 знаков):
1. Эмоциональное погружение и повод (2 предложения).
2. Флористическая эстетика и состав, оформление (плёнка или минималистичная лента в стиле REN).
3. Индивидуальный уход за составом (2 конкретные рекомендации).
4. SEO-завершение: быстрая бережная доставка по Санкт-Петербургу в фирменной упаковке.
Ответ — только текст описания."""


def _fallback_description(name: str, color_mood: str, composition_str: str,
                          packaging_type: str, platform: str) -> str:
    from platform_rules import _instrumental
    items = [s.strip() for s in composition_str.split(",") if s.strip()]
    # падеж для «в … гамме/оттенках»: Кремовый -> кремовой, Розовый -> розовой
    adj = color_mood.lower()
    if adj.endswith("ый"):
        adj = adj[:-2] + "ой"
    elif adj.endswith("ий"):
        adj = adj[:-2] + "ей"
    if platform == "market":
        return (
            f"Авторский букет «{name}» от цветочной студии REN в Санкт-Петербурге.\n\n"
            f"Состав букета: {composition_str}. Оформление: {packaging_type.lower()}.\n\n"
            f"Композиция в {adj} гамме подойдёт на день рождения, юбилей, свидание "
            f"или как знак внимания без повода — для любимой, мамы, подруги или коллеги. "
            f"Цветы утренней поставки отбираются флористом вручную, поэтому букет долго держит "
            f"форму и насыщенность оттенков.\n\n"
            f"Уход за букетом: {care_tips([{'name': n} for n in items])}. "
            f"В комплекте — кризал для продления стойкости цветов.\n\n"
            f"{_standard_block()}"
        )
    # Flowwow: 4-блочная SEO-структура по инструкции владельца (08.11.2026)
    flower_names = [re.sub(r"\s*—\s*\d+\s*шт\.?$", "", it).strip() for it in items]
    lead_pair = " и ".join(_instrumental(f) for f in flower_names[:2]) if flower_names else name.lower()
    consist_lines = "\n".join(f"— {it}" for it in items) if items else f"— {name}"
    return (
        f"Авторский букет с {lead_pair} в {adj} гамме от флористической студии "
        f"REN в Санкт-Петербурге. Изысканный подарок для любимой девушки, мамы, супруги или коллеги "
        f"на день рождения, романтическое свидание, годовщину или элегантный комплимент без повода.\n\n"
        f"В составе букета:\n{consist_lines}.\n"
        f"Оформление: {packaging_type.lower()} в эстетике современного флористического минимализма.\n\n"
        f"Индивидуальные рекомендации по уходу:\n{care_tips([{'name': n} for n in items])}. "
        f"В комплекте — кризал для продления стойкости цветов.\n\n"
        f"{_standard_block()}"
    )


async def _llm_description(name: str, color_mood: str, composition_str: str,
                           packaging_type: str) -> Optional[str]:
    prompt = PROMPT.format(bouquet_name=name, color_mood=color_mood,
                           cleaned_composition=composition_str, packaging_type=packaging_type)
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{config.openai_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {config.openai_api_key}"},
                json={"model": config.openai_model,
                      "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0.7, "max_tokens": 600},
            )
            if resp.status_code != 200:
                logger.warning(f"LLM описаний: HTTP {resp.status_code}: {resp.text[:200]}")
                return None
            content = resp.json()["choices"][0]["message"]["content"].strip()
            return content if 200 < len(content) < 2000 else None
    except Exception as e:
        logger.warning(f"LLM описаний недоступен, беру шаблон: {e}")
        return None


async def generate_description(name: str, color_mood: str, composition_str: str,
                               packaging_type: str, platform: str = "flowwow") -> str:
    """Уникальное описание букета: LLM (если настроен ключ) или фирменный шаблон."""
    if config.openai_api_key:
        text = await _llm_description(name, color_mood, composition_str, packaging_type)
        if text:
            return text
    return _fallback_description(name, color_mood, composition_str, packaging_type, platform)
