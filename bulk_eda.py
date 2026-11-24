"""Разовый пакетный экспорт товаров Flowwow -> CSV импорта Яндекс Еды (27 колонок).

Запуск:  python bulk_eda.py [out.csv]
Токен/магазин берутся из .secrets.local (FLOWWOW_TOKEN, FLOWWOW_SHOP_ID).
Правила: цена Еды = base x 1.3, где base = fw_price/1.5 - 150 (округление до десятков);
вес всегда 1 кг; описание — полное описание Flowwow; фото — CDN Flowwow.
"""

import csv
import json
import os
import re
import sys
import time
import urllib.request

from eda_csv import CAT_IDS, PLACES, _plural_gen
from platform_rules import clean_position_name
from market_api import match_flower_kinds

# Официальный шаблон импорта PickerApp (cvetImport.csv от поддержки, 24.11):
# разделитель ЗАПЯТАЯ, UTF-8 БЕЗ BOM, заголовки короткие (без многострочных примечаний).
# Наш прежний формат (точка с запятой + заголовки из выгрузки ЛК) импортер отклоняет
# с «Invalid request» — это формат ЭКСПОРТА, а не импорта.
TEMPLATE_HEADERS = [
    "Название категории 1 уровень", "id категории 1 уровень",
    "Название категории 2 уровень", "id категории 2 уровень",
    "Название категории 3 уровень", "id категории 3 уровень",
    "Название позиции", "id позиции", "Общий вес позиции", "Единица измерения",
    "Квантовка", "Цена", "Описание товара", "Производитель", "Страна", "Штрихкоды",
    "Товар доступен в магазинах", "Акциза", "Ссылки на картинки",
    "Налоговые коды товаров (для Узбекистана)", "Налоговые коды упаковок (для Узбекистана)",
    "Требуется маркировка (для Узбекистана)", "Товар для взрослых",
    "Кол-во товаров в упаковке", "Ширина (см)", "Высота (см)",
    "Состав букета (в формате: Наименование позиции:кол-во,Наименование позиции:кол-во,"
    "...и т.д.. Без пробелов. Например Роза пионовидная:10,Ромашка:5,... и т.д..) "
    "Наименование позиции нужно брать из справочника",
]

BASE = "https://apis.flowwow.com/apiseller"


def load_secrets(path=".secrets.local"):
    vals = {}
    for line in open(path, encoding="utf-8"):
        if "=" in line:
            k, v = line.strip().split("=", 1)
            vals[k] = v
    return vals


def api_post(path, body, token, retries=5):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                BASE + path, data=json.dumps(body).encode(), method="POST",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = 5 * (attempt + 1)
            print(f"  сбой сети ({e}); повтор через {wait}с", flush=True)
            time.sleep(wait)


def fetch_all_active(token, shop_id):
    """Пагинация по page (offset API игнорирует — проверено 24.11), дедуп по productId."""
    items, seen, page = [], set(), 1
    while True:
        d = api_post(f"/products?shopId={shop_id}",
                     {"status": "active", "page": page, "limit": 50}, token)
        batch = d.get("items") or []
        if not batch:
            break
        new = 0
        for p in batch:
            pid = p.get("productId")
            if pid not in seen:
                seen.add(pid)
                items.append(p)
                new += 1
        print(f"  страница {page}: +{new} (всего {len(items)} из {d.get('total', '?')})", flush=True)
        if len(batch) < 50 or new == 0:
            break
        page += 1
        time.sleep(1)  # бережём rate-limit
    return items


MONO_RX = re.compile(r"моно", re.I)
QTY_RX = re.compile(r"\b(\d{1,3})\s*(?:шт|тюльпан|роз|гвоздик|хризантем|пион|эустом|ранункул|альстр|гербер)")


def build_row(item):
    """Одна строка CSV из карточки Flowwow. None — если пропускаем."""
    name = (item.get("name") or "").strip()
    images = [u for u in (item.get("images") or []) if u][:10]
    try:
        fw_price = float(item.get("price") or 0)
    except (TypeError, ValueError):
        fw_price = 0
    base = int(round((fw_price / 1.5 - 150) / 10) * 10) if fw_price else 0
    if not name or not images or base <= 0:
        return None, "нет названия/фото/цены"

    eda_price = int(round(base * 1.3 / 10) * 10)
    desc = (item.get("description") or "").strip() or name

    # Состав: consist карточки, иначе «Состав: А — 2 шт» из описания,
    # иначе виды цветов из НАЗВАНИЯ (компоненты обязаны быть из справочника Еды —
    # заглушка «название:1» отклоняет весь импорт, проверено 24.11)
    dpos = []
    for c in item.get("consist") or []:
        try:
            dpos.append((clean_position_name(str(c.get("name", ""))), int(c.get("quantity", 1))))
        except (TypeError, ValueError):
            continue
    if not dpos:
        m_comp = re.search(r"Состав:\s*(.+?)(?:\.\s|\.$|$)", desc, re.S)
        if m_comp:
            for chunk in re.split(r",|\n", m_comp.group(1)):
                m_q = re.match(r"\s*(.+?)\s*[—-]\s*(\d+)\s*шт", chunk.strip())
                if m_q:
                    dpos.append((clean_position_name(m_q.group(1)), int(m_q.group(2))))
    if not dpos:
        # Виды цветов из НАЗВАНИЯ: матчим по кускам (запятые/тире/«и»), чтобы зелень
        # в конце названия не глушила весь матч (в match_flower_kinds стоп-слова
        # отсекают строку целиком — она для позиций, а не для названий)
        chunks = re.split(r"[,;/—–\-]|\sи\s", name.lower())
        kinds, seen_k = [], set()
        for ch in chunks:
            for _, canon in match_flower_kinds([ch]):
                if canon not in seen_k:
                    seen_k.add(canon)
                    kinds.append(canon)
        if kinds:
            m_q = re.search(r"\b(\d{1,3})\s*(?:шт|штук)", name.lower())
            qty = int(m_q.group(1)) if m_q else 0
            if len(kinds) == 1 and qty:
                dpos = [(kinds[0], qty)]
            else:
                dpos = [(k, 1) for k in kinds[:6]]
    if not dpos:
        return None, "не удалось определить состав (нет цветов в названии)"

    # Моно: слово «моно» в названии или один вид цветов в составе
    is_mono = bool(MONO_RX.search(name)) or (len(dpos) == 1)
    if is_mono:
        cat = "Монобукеты"
        qty = sum(q for _, q in dpos) if dpos else 0
        if not qty:
            m_q = QTY_RX.search(name.lower())
            qty = int(m_q.group(1)) if m_q else 1
        flower = dpos[0][0].split()[0] if dpos else name[:40]
        title = f"Букет из {qty} {_plural_gen(flower)}"[:100]
        width, height = 25, 45
    else:
        cat = "Авторские букеты"
        title = name[:100]
        width, height = 35, 45

    consist_str = ",".join(f"{n[:1].upper() + n[1:]}:{q}" for n, q in dpos)

    return [cat, CAT_IDS[cat], "", "", "", "",
            title, "",
            "1000", "г", "",
            str(eda_price),
            desc[:3000].rsplit(". ", 1)[0] + "." if len(desc) > 3000 else desc,
            "REN", "Россия",
            "", PLACES, "",
            ",".join(images),
            "", "", "",
            "", "1",
            str(width), str(height),
            consist_str], ""


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "eda_bulk.csv"
    sec = load_secrets()
    token, shop_id = sec["FLOWWOW_TOKEN"], sec["FLOWWOW_SHOP_ID"]

    print("Загружаю активные товары Flowwow...", flush=True)
    items = fetch_all_active(token, shop_id)
    print(f"Всего активных: {len(items)}")

    rows, skipped = [], []
    for it in items:
        row, why = build_row(it)
        if row:
            rows.append(row)
        else:
            skipped.append((it.get("productId"), (it.get("name") or "")[:60], why))

    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=",")
        w.writerow(TEMPLATE_HEADERS)
        w.writerows(rows)

    mono = sum(1 for r in rows if r[0] == "Монобукеты")
    print(f"OK: {out} — строк: {len(rows)} (моно {mono}, авторские {len(rows) - mono}), пропущено: {len(skipped)}")
    for pid, nm, why in skipped[:20]:
        print(f"  пропуск {pid}: {nm} ({why})")


if __name__ == "__main__":
    main()
