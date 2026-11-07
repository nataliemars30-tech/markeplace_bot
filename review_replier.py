"""Генератор черновиков ответов на отзывы Flowwow.

Режимы:
- Если задан OPENAI_API_KEY (OpenAI-совместимый endpoint, напр. Kimi/Moonshot) —
  черновик пишет LLM по фирменному промту.
- Иначе — тёплый шаблон по оценке (5/4/3 и ниже).

Черновик ВСЕГДА уходит флористу на согласование: автопубликация без /ok не делается.
"""

import logging
from typing import Any, Dict, Optional

import httpx

from config import config

logger = logging.getLogger(__name__)

PROMPT = """Ты — SMM-специалист цветочной студии REN из Санкт-Петербурга.
Напиши короткий (1-3 предложения) ответ магазина на отзыв покупателя Flowwow.
Тон: тёплый, живой, без канцелярита, можно одно уместное эмодзи (🌸💐).
НЕ используй слова «бесплатно», «скидка», «подарок» (маркетплейсы их режут).
Если оценка 5 — поблагодари и пригласи снова. Если 4 — поблагодари, покажи, что нам важно мнение.
Если 3 и ниже — искренне извинись, поблагодари за обратную связь и предложи решить вопрос
(попросить написать в поддержку магазина через чат Flowwow). Не оправдывайся.
Подпишись «Команда REN». Ответ только текстом ответа, без пояснений.
"""


def _template_draft(review: Dict[str, Any]) -> str:
    score = int(review.get("shopScore") or 0)
    author = (review.get("authorName") or "").strip()
    greet = f"{author}, спасибо" if author else "Спасибо"
    if score >= 5:
        return (f"{greet} за такой тёплый отзыв! Нам очень приятно, что букет понравился — "
                f"передадим ваши слова флористу, который его собирал 💐 "
                f"Будем рады видеть вас снова! Команда REN")
    if score == 4:
        return (f"{greet} за отзыв и высокую оценку! Нам важно каждое мнение — "
                f"обязательно учтём ваш комментарий. Ждём вас снова! Команда REN")
    return (f"{greet}, что поделились опытом — нам жаль, что заказ не оправдал ожиданий. "
            f"Напишите, пожалуйста, нам через чат Flowwow, мы обязательно всё решим. "
            f"Команда REN")


async def _llm_draft(review: Dict[str, Any]) -> Optional[str]:
    """Черновик через OpenAI-совместимый API. None при любой ошибке (фолбэк на шаблон)."""
    score = int(review.get("shopScore") or 0)
    author = (review.get("authorName") or "Аноним").strip() or "Аноним"
    text = (review.get("text") or "").strip() or "(без текста)"
    user = (f"Оценка: {score}/5. Автор: {author}.\nОтзыв: {text}\n\nНапиши ответ от имени магазина.")
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{config.openai_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {config.openai_api_key}"},
                json={
                    "model": config.openai_model,
                    "messages": [
                        {"role": "system", "content": PROMPT},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.7,
                    "max_tokens": 300,
                },
            )
            if resp.status_code != 200:
                logger.warning(f"LLM отзывов: HTTP {resp.status_code}: {resp.text[:200]}")
                return None
            content = resp.json()["choices"][0]["message"]["content"]
            return content.strip().strip('"')
    except Exception as e:
        logger.warning(f"LLM отзывов недоступен, беру шаблон: {e}")
        return None


async def make_draft(review: Dict[str, Any]) -> str:
    """Черновик ответа: LLM, если настроен ключ, иначе шаблон."""
    if config.openai_api_key:
        draft = await _llm_draft(review)
        if draft:
            return draft
    return _template_draft(review)
