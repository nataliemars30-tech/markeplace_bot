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

from eda_csv import HEADERS_27, CAT_IDS, PLACES, _plural_gen
from platform_rules import clean_position_name

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

    # Состав: consist карточки, иначе «Состав: А — 2 шт» из описания
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

    consist_str = ",".join(f"{n}:{q}" for n, q in dpos) or f"{title}:1"

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

    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(HEADERS_27)
        w.writerows(rows)

    mono = sum(1 for r in rows if r[0] == "Монобукеты")
    print(f"OK: {out} — строк: {len(rows)} (моно {mono}, авторские {len(rows) - mono}), пропущено: {len(skipped)}")
    for pid, nm, why in skipped[:20]:
        print(f"  пропуск {pid}: {nm} ({why})")


if __name__ == "__main__":
    main()
