"""SEO-генератор описаний букетов по ТЗ 07.11.2026.

Если задан OPENAI_API_KEY (OpenAI-совместимый API: Kimi/Moonshot/OpenAI) —
описания пишет LLM по строгому фирменному промпту. Иначе — качественный шаблон.
Оба варианта: БЕЗ открыток (запрещено ТЗ), уход точечно под состав.
"""

import logging
from typing import Any, Dict, List, Optional

import httpx

from config import config
from platform_rules import care_tips

logger = logging.getLogger(__name__)

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
4. SEO-завершение: быстрая бережная доставка по Санкт-Петербургу в транспортировочном аквабоксе.
Ответ — только текст описания."""


def _fallback_description(name: str, color_mood: str, composition_str: str,
                          packaging_type: str, platform: str) -> str:
    if platform == "market":
        return (
            f"Авторский букет «{name}» от цветочной студии REN в Санкт-Петербурге.\n\n"
            f"Состав букета: {composition_str}. Оформление: {packaging_type.lower()}.\n\n"
            f"Композиция в {color_mood.lower()}й гамме подойдёт на день рождения, юбилей, свидание "
            f"или как знак внимания без повода — для любимой, мамы, подруги или коллеги. "
            f"Цветы утренней поставки отбираются флористом вручную, поэтому букет долго держит "
            f"форму и насыщенность оттенков.\n\n"
            f"Уход за букетом: {care_tips([{'name': n} for n in composition_str.split(',')])}. "
            f"В комплекте — кризал для продления стойкости цветов.\n\n"
            f"Доставка по Санкт-Петербургу в транспортировочном аквабоксе с водой — цветы приезжают "
            f"такими же свежими, какими покинули студию."
        )
    return (
        f"Авторский букет «{name}» от цветочной студии REN, Санкт-Петербург 🌿\n\n"
        f"Состав: {composition_str}. Игра оттенков в {color_mood.lower()}й гамме, воздушная фактура "
        f"и фирменная сборка REN — композиция выглядит так, будто её только собрали в саду 🌸\n\n"
        f"Кому дарить: любимой 💕, маме 🤱, подруге, коллеге. Повод: день рождения 🎂, юбилей, "
        f"свидание — или просто без повода ✨\n\n"
        f"Уход за букетом: {care_tips([{'name': n} for n in composition_str.split(',')])}. "
        f"В комплекте — кризал для продления стойкости цветов.\n\n"
        f"Доставляется по Санкт-Петербургу в транспортировочном аквабоксе с водой 💐"
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
