"""Разовое исправление названий карточек Яндекс Маркета (контент-рейтинг, нарушения правил).

Чинит: «Букет Букет», запрещённое «— купить с доставкой в СПб», эмодзи,
сломанные склонения («с неринеом», «с молюцелаом», «с хрхом»), названия вне схемы.
Схема Маркета: «Букет REN «Имя» размер X с [состав твор. падеж]» (75-256 зн., без эмодзи/региона).

Запуск: python fix_market_names.py          — dry-run (печать старое -> новое)
        python fix_market_names.py apply    — применить
"""

import asyncio
import json
import re
import sys
import types

import httpx

sys.modules.setdefault("config", types.ModuleType("config"))
sys.modules["config"].config = type("C", (), {"openai_api_key": ""})()

from platform_rules import build_marketplace_title, _instrumental  # noqa: E402

KEY = "ACMA:ysRdbQ8pShodeZQhhiiveJHTyqnDlLfEhURz0DGJ:d685be2b"
BID = 216468683
H = {"Api-Key": KEY, "Content-Type": "application/json"}
PARAM_SOSTAV = 84321304

EMOJI_RX = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️←-⇿⌀-⏿]")


def clean_old_name(name: str) -> str:
    n = EMOJI_RX.sub("", name or "")
    n = re.sub(r"\s{2,}", " ", n).strip(" ,-–—")
    return n


def extract_display_name(oid: str, name: str) -> str:
    """Имя букета для «кавычек»: из «...», иначе из offerId/старого названия."""
    m = re.search(r"[«\"]([^»\"]{2,40})[»\"]", name or "")
    if m:
        nm = m.group(1).strip()
        if nm.lower() not in ("ren",):
            return nm
    # из offerId: Country_Blues_7 -> Country Blues
    base = re.sub(r"_?\d+$", "", oid).replace("_", " ").strip()
    base = re.sub(r"\s*(size\s*)?[SML]$", "", base, flags=re.I).strip()
    if re.fullmatch(r"[A-Za-z0-9 ()'&.-]{2,40}", base) and not base.upper().startswith(("REN-", "MRKT-")):
        return base
    # из названия: «Букет <Имя> - состав» (тире только с пробелами — «розово-зеленый» не имя)
    m = re.match(r"(?:Роскошный |Авторский )?Букет(?: авторский| нежный| премиум)?\s+"
                 r"(?:REN\s+)?[\"«']?([A-Za-zА-Яа-я0-9 '&.]{2,35}?)[\"»']?\s+[-–—(]", name or "")
    if m:
        cand = m.group(1).strip()
        cand = re.sub(r"^букет\s+", "", cand, flags=re.I).strip()  # «Букет Букет Мужской»
        if cand.lower() not in ("ren", "букет", "to", "togo") and len(cand) >= 2:
            return cand
    return ""


def extract_size(oid: str, name: str) -> str:
    m = re.search(r"-([SML])$", oid)
    if m:
        return m.group(1)
    m = re.search(r"\b(size\s*)?([SML])\b\s*[)-]?", name or "", re.I)
    if m and m.group(2):
        return m.group(2).upper()
    m = re.search(r"\b(Medium|Large|Small)\b", name or "", re.I)
    if m:
        return {"small": "S", "medium": "M", "large": "L"}[m.group(1).lower()]
    return "M"


def parse_sostav_param(card) -> list:
    """Состав из характеристики карточки: 'Роза — 2 шт, ...' -> [(имя, qty)]."""
    out = []
    for pv in card.get("parameterValues") or []:
        if pv.get("parameterId") != PARAM_SOSTAV:
            continue
        val = pv.get("value") or ""
        for chunk in re.split(r"[,;]\s*", val):
            m = re.match(r"\s*(.+?)\s*[—-]\s*(\d+)\s*шт", chunk)
            if m:
                out.append((m.group(1).strip(), int(m.group(2))))
    return out


def parse_sostav_name(name: str) -> list:
    """Состав из старого названия: хвост после '-' или ' из/с '."""
    n = clean_old_name(name)
    m = re.split(r"\s[-–—]\s|\s+из\s+|\s+с\s+", n, maxsplit=1)
    tail = m[1] if len(m) > 1 else ""
    tail = re.sub(r"\(.*?\)", "", tail)
    parts = [p.strip() for p in re.split(r",|\sи\s", tail) if p.strip()]
    out = []
    for p in parts[:7]:
        p = re.sub(r"\d+\s*(шт|цветков|см)\.?", "", p).strip(" .#№")
        if 2 < len(p) < 40:
            out.append((p, 1))
    return out


GREEN_SKIP = ("эвкалипт", "писташ", "зелень", "листья", "лист", "дуб", "статица", "лаванда",
              "лемониум", "лимониум", "озотамнус", "берграсс", "гиперикум")

# расходники/упаковка — НЕ состав букета, в название не идут никогда
EXPENDABLES = ("упаковк", "тишью", "лента", "лентой", "кризал", "аквабокс", "открытка",
               "коробка", "оазис", "бант", "подкормка", "дизайнерская", "крафт", "плёнка", "пленка")

# не-букеты: их названия не трогаем (своя схема, рейтинг там не из-за названия)
NON_BOUQUET = ("ваза", "кружка", "кашпо", "растение", "антуриум", "сингониум", "стефанотис",
               "непентес", "мухоловка", "дионея", "мини-ваза", "цветущая", "корзина пустая")


def is_bouquet(oid: str, name: str) -> bool:
    low = (name or "").lower()
    if any(w in low for w in NON_BOUQUET):
        return False
    return any(w in low for w in ("букет", "монобукет", "корзина", "композиция")) or oid.upper().startswith("REN-")


def build_new_name(oid: str, old: str, card) -> str:
    # состав только из характеристики карточки (там единственное число) и без расходников
    dpos_raw = [(n, q) for n, q in parse_sostav_param(card)
                if not any(e in n.lower() for e in EXPENDABLES)]
    dpos = [{"name": n, "qty": q} for n, q in dpos_raw]
    display = extract_display_name(oid, clean_old_name(old))
    if len(display) < 3:
        display = ""
    size = extract_size(oid, old)
    low = clean_old_name(old).lower()
    is_mono = "монобукет" in low or bool(re.search(r"\b\d+\s*(шт|цветков)\b", low) and len(dpos_raw) <= 1)
    is_ren = oid.upper().startswith("REN-")

    if is_mono:
        qty = 0
        m_q = re.search(r"\b(\d{1,3})\s*(шт|цветков)\b", low)
        if m_q:
            qty = int(m_q.group(1))
        elif dpos_raw:
            qty = dpos_raw[0][1]
        if not qty:
            qty = 1
        base = f"Монобукет REN «{display}»" if display else "Монобукет REN"
        base += f", {qty} шт"
        if dpos_raw:
            base += f" — {dpos_raw[0][0].lower()}"
        h = re.search(r"(\d{2})\s*см", low)
        if h:
            base += f", высота {h.group(1)} см"
        new = base
    else:
        if not display:
            return ""
        # состав в название — только для карточек бота (REN-*) с чистым параметром
        flowers = [p for p in dpos if not any(g in p["name"].lower() for g in GREEN_SKIP)]
        if is_ren and flowers:
            new = build_marketplace_title(display, "", flowers,
                                          market_style=True, size_label=size, limit=200)
        else:
            new = f"Букет REN «{display}» размер {size}"
    new = clean_old_name(new)
    if len(new) < 75:
        new = (new + " — авторская сборка цветочной студии REN, свежие цветы утренней поставки").strip(" —")
    return new[:250]


async def fetch_all(c):
    cards = {}
    pt = None
    for _ in range(15):
        body = {"limit": 100}
        if pt:
            body["pageToken"] = pt
        r = await c.post(f"https://api.partner.market.yandex.ru/v2/businesses/{BID}/offer-cards",
                         headers=H, json=body)
        res = r.json().get("result") or {}
        new = 0
        for cd in res.get("offerCards") or []:
            if cd["offerId"] not in cards:
                cards[cd["offerId"]] = cd
                new += 1
        npt = (res.get("paging") or {}).get("nextPageToken")
        if not npt or new == 0:
            break
        pt = npt
    names = {}
    ids = list(cards)
    for i in range(0, len(ids), 100):
        r = await c.post(f"https://api.partner.market.yandex.ru/v2/businesses/{BID}/offer-mappings",
                         headers=H, json={"offerIds": ids[i:i + 100]})
        for m in (r.json().get("result") or {}).get("offerMappings") or []:
            o = m.get("offer") or {}
            names[o.get("offerId")] = o
    return cards, names


def needs_fix(oid: str, name: str, rating: int) -> bool:
    if rating >= 95 or not name:
        return False
    low = name.lower()
    if "купить" in low or "доставк" in low or "спб" in low or "санкт-петербург" in low:
        return True
    if EMOJI_RX.search(name):
        return True
    if re.search(r"\bбукет\s+букет\b", low):
        return True
    if re.search(r"неринеом|молюцелаом|хрхом|гортензи\s|гортензи\b", low):
        return True
    if not re.match(r"(Букет|Монобукет|Корзина|Композиция)\b", name):
        return True
    if "ren" not in low and "букет" in low and "мрк" not in oid.lower():
        return True
    return False


async def main(apply: bool):
    async with httpx.AsyncClient(timeout=60) as c:
        cards, offers = await fetch_all(c)
        plan = []
        skipped_non_bouquet = []
        for oid, offer in offers.items():
            old = offer.get("name") or ""
            rating = (cards.get(oid) or {}).get("contentRating") or 0
            if not needs_fix(oid, old, rating):
                continue
            if not is_bouquet(oid, old):
                skipped_non_bouquet.append((oid, old[:70], rating))
                continue
            new = build_new_name(oid, old, cards.get(oid) or {})
            if new and new != old:
                plan.append((oid, old, new, rating))
        print(f"к переименованию: {len(plan)} | пропущено не-букетов: {len(skipped_non_bouquet)}\n")
        for oid, nm, rt in skipped_non_bouquet:
            print(f"  (пропуск, не букет) [{rt}] {oid}: {nm}")
        for oid, old, new, rating in sorted(plan, key=lambda x: x[3]):
            print(f"[{rating}] {oid}")
            print(f"  БЫЛО: {old[:110]}")
            print(f"  СТАЛО: {new[:110]}\n")
        if not apply:
            print("dry-run. Для применения: python fix_market_names.py apply")
            return
        # применяем батчами по 10
        ok = err = 0
        for i in range(0, len(plan), 10):
            batch = []
            for oid, old, new, rating in plan[i:i + 10]:
                offer = dict(offers[oid])
                for k in ("customsCommodityCode", "campaigns", "cardStatus", "mediaFiles"):
                    offer.pop(k, None)
                offer["name"] = new
                batch.append({"offer": offer})
            r = await c.post(
                f"https://api.partner.market.yandex.ru/v2/businesses/{BID}/offer-mappings/update",
                headers=H, json={"offerMappings": batch})
            d = r.json()
            for res in (d.get("result") or {}).get("results") or []:
                if res.get("errors"):
                    err += 1
                    print("ОШИБКА", res.get("offerId"), json.dumps(res["errors"], ensure_ascii=False)[:150])
                else:
                    ok += 1
            await asyncio.sleep(1)
        print(f"\nобновлено: {ok}, ошибок: {err}")


if __name__ == "__main__":
    asyncio.run(main(len(sys.argv) > 1 and sys.argv[1] == "apply"))
