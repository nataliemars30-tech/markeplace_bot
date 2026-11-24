"""Дозаполнение характеристик карточек Яндекс Маркета до максимального контент-рейтинга.

- Вазы/кашпо: набор 100-балльного эталона (Тип, Материал, Высота/Ширина, Цвета,
  Размещение, Ед./Упаковок, краткое описание).
- Букеты: полный бот-набор (Кому x9, Повод x18, Оформление, Сборка, Размер,
  Вид цветов, Зелень, Кол-во, Ед./Упаковок, Прочие, Доп.информация).
- Описания <500 зн. дополняются (без эмодзи, региона и «купить» — правила Маркета).

Запуск: python upgrade_market_cards.py [apply]
"""

import asyncio
import json
import re
import sys
import types

sys.modules.setdefault("config", types.ModuleType("config"))
sys.modules["config"].config = type("C", (), {"openai_api_key": ""})()

import httpx  # noqa: E402
from market_api import (PARAMS, SIZE_VALUE_IDS, OFORM_IDS, TYPE_IDS,  # noqa: E402
                        ALL_AUDIENCE, ALL_OCCASIONS, match_flower_kinds, GREEN_KIND_MAP)

KEY = "ACMA:ysRdbQ8pShodeZQhhiiveJHTyqnDlLfEhURz0DGJ:d685be2b"
BID = 216468683
H = {"Api-Key": KEY, "Content-Type": "application/json"}
CAT_FLOWERS, CAT_VASE = 91284, 61853873

VASE_TYPE = 21194330
VASE_MATERIAL = 17352854
VASE_PLACE = 27142752      # размещение: настольная 28737693
VASE_CLEAR = 27140831      # прозрачность: прозрачная 45183452
VASE_SHORT = 200           # краткое описание
FILTER_COLORS = {"бесцвет": 17584838, "прозрач": 17584838, "черн": 13899071, "дымк": 13899071,
                 "бел": 13887686, "беж": 13887677, "розов": 13891903, "красн": 13891866,
                 "зелен": 13891809, "желт": 13891805, "жёлт": 13891805, "сирен": 13898990}
CARD_COLORS = {"бел": (17946836, "белый"), "розов": (17367592, "розовый"),
               "красн": (14898056, "красный"), "сирен": (14896158, "сиреневый"),
               "желт": (15271148, "жёлтый"), "жёлт": (15271148, "жёлтый"),
               "беж": (14898800, "светло-бежевый"), "крем": (14898800, "светло-бежевый")}

DESC_EXT = ("\n\nБукет собирается флористом в день отправки из свежих цветов утренней поставки. "
            "Состав и оттенки могут незначительно меняться в зависимости от поставки — флорист "
            "сохраняет настроение и цветовую гамму композиции. Перед постановкой в вазу обновите "
            "срез стеблей на 1-2 см под углом и используйте чистую прохладную воду.")


def dims_from_name(name):
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*[*xх×-]\s*(\d+(?:[.,]\d+)?)", name)
    if m:
        a, b = float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))
        return int(min(a, b)), int(max(a, b))
    m = re.search(r"\b(\d{2})\s*см", name)
    return (12, int(m.group(1))) if m else (12, 20)


def vase_additions(name, have):
    low = name.lower()
    add = []
    w, h = dims_from_name(name)
    is_kashpo = "кашпо" in low
    if VASE_TYPE not in have:
        add.append({"parameterId": VASE_TYPE, "value": "кашпо"} if is_kashpo
                   else {"parameterId": VASE_TYPE, "valueId": 34061561, "value": "ваза"})
    if VASE_MATERIAL not in have:
        if "керамик" in low:
            add.append({"parameterId": VASE_MATERIAL, "value": "керамика"})
        else:
            add.append({"parameterId": VASE_MATERIAL, "valueId": 17362559, "value": "стекло"})
    if 14805336 not in have:
        add.append({"parameterId": 14805336, "unitId": 8, "value": str(h)})
    if 23681530 not in have:
        add.append({"parameterId": 23681530, "unitId": 8, "value": str(w)})
    if 14805583 not in have:
        add.append({"parameterId": 14805583, "unitId": 6145205, "value": "1"})
    if 23674510 not in have:
        add.append({"parameterId": 23674510, "unitId": 6146430, "value": "1"})
    if 33663230 not in have:
        add.append({"parameterId": 33663230, "unitId": 6145205, "value": "1"})
    if VASE_PLACE not in have:
        add.append({"parameterId": VASE_PLACE, "valueId": 28737693, "value": "настольная"})
    if VASE_CLEAR not in have and ("прозрач" in low or "бесцвет" in low):
        add.append({"parameterId": VASE_CLEAR, "valueId": 45183452, "value": "прозрачная"})
    if 13887626 not in have:
        for key, vid in FILTER_COLORS.items():
            if key in low:
                add.append({"parameterId": 13887626, "valueId": vid, "value": key})
                break
    if 14871214 not in have:
        for key, (vid, val) in CARD_COLORS.items():
            if key in low:
                add.append({"parameterId": 14871214, "valueId": vid, "value": val})
                break
    if VASE_SHORT not in have:
        add.append({"parameterId": VASE_SHORT, "value": name[:60]})
    return add


def flower_additions(oid, name, card, have_map):
    low = name.lower()
    sostav = ""
    for pv in card.get("parameterValues") or []:
        if pv.get("parameterId") == PARAMS["Состав"]:
            sostav = pv.get("value") or ""
    src = sostav or name
    add = []
    for vid, vname in ALL_AUDIENCE:
        if ("Кому", vid) not in have_map:
            add.append({"parameterId": PARAMS["Кому"], "valueId": vid, "value": vname})
    for vid, vname in ALL_OCCASIONS:
        if ("Повод", vid) not in have_map:
            add.append({"parameterId": PARAMS["Повод"], "valueId": vid, "value": vname})
    if PARAMS["Оформление"] not in have_map:
        add.append({"parameterId": PARAMS["Оформление"], "valueId": OFORM_IDS["Пленка"], "value": "пленка"})
    if PARAMS["Сборка"] not in have_map:
        add.append({"parameterId": PARAMS["Сборка"], "valueId": 44625852, "value": "на стеблях"})
    m = re.search(r"-([SML])$", oid) or re.search(r"\b([SML])\b", name)
    if PARAMS["Размер"] not in have_map and m:
        lab = m.group(1).upper()
        if lab in SIZE_VALUE_IDS:
            add.append({"parameterId": PARAMS["Размер"], "valueId": SIZE_VALUE_IDS[lab], "value": lab})
    existing_kinds = {v for (p, v) in have_map if p == "ВидЦ"}
    for vid, canon in match_flower_kinds([src]):
        if vid not in existing_kinds:
            add.append({"parameterId": PARAMS["Вид цветов"], "valueId": vid, "value": canon})
    for phrase, vid, canon in GREEN_KIND_MAP:
        if vid and phrase in src.lower() and ("Зел", vid) not in have_map:
            add.append({"parameterId": PARAMS["Зелень"], "valueId": vid, "value": canon})
    if PARAMS["Количество цветов"] not in have_map:
        qty = sum(int(q) for q in re.findall(r"[—-]\s*(\d+)\s*шт", sostav)) if sostav else 0
        if qty:
            add.append({"parameterId": PARAMS["Количество цветов"], "value": str(qty)})
    if PARAMS["Высота букета"] not in have_map:
        mh = re.search(r"(\d{2})\s*см", low)
        add.append({"parameterId": PARAMS["Высота букета"], "value": mh.group(1) if mh else "45"})
    if PARAMS["Тип"] not in have_map:
        t = TYPE_IDS["mono" if "монобукет" in low or "моно" in low else "author"]
        add.append({"parameterId": PARAMS["Тип"], "valueId": t[0], "value": t[1]})
    if PARAMS["Единиц в одной упаковке"] not in have_map:
        add.append({"parameterId": PARAMS["Единиц в одной упаковке"], "value": "1"})
    if PARAMS["Количество упаковок в товаре"] not in have_map:
        add.append({"parameterId": PARAMS["Количество упаковок в товаре"], "value": "1"})
    if PARAMS["Прочие характеристики"] not in have_map:
        add.append({"parameterId": PARAMS["Прочие характеристики"],
                    "value": "Вариативность состава:состав и оттенки могут незначительно меняться "
                             "в зависимости от поставки, флорист сохраняет гамму букета"})
    if PARAMS["Дополнительная информация"] not in have_map:
        add.append({"parameterId": PARAMS["Дополнительная информация"],
                    "value": "Букет собирается флористом в день доставки. Цветочная студия REN."})
    return add


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
    offers = {}
    ids = list(cards)
    for i in range(0, len(ids), 100):
        r = await c.post(f"https://api.partner.market.yandex.ru/v2/businesses/{BID}/offer-mappings",
                         headers=H, json={"offerIds": ids[i:i + 100]})
        for m in (r.json().get("result") or {}).get("offerMappings") or []:
            o = m.get("offer") or {}
            offers[o.get("offerId")] = (o, (m.get("mapping") or {}).get("marketCategoryId"))
    return cards, offers


async def main(apply):
    async with httpx.AsyncClient(timeout=90) as c:
        cards, offers = await fetch_all(c)
        plan = []
        for oid, (offer, cat) in offers.items():
            cd = cards.get(oid) or {}
            rating = cd.get("contentRating") or 0
            if rating >= 100 or oid.startswith("VASE-"):
                continue
            name = offer.get("name") or ""
            pvs = cd.get("parameterValues") or []
            have = {pv["parameterId"] for pv in pvs}
            have_map = set()
            for pv in pvs:
                if pv["parameterId"] == PARAMS["Кому"]:
                    have_map.add(("Кому", pv.get("valueId")))
                if pv["parameterId"] == PARAMS["Повод"]:
                    have_map.add(("Повод", pv.get("valueId")))
                if pv["parameterId"] == PARAMS["Вид цветов"]:
                    have_map.add(("ВидЦ", pv.get("valueId")))
                if pv["parameterId"] == PARAMS["Зелень"]:
                    have_map.add(("Зел", pv.get("valueId")))
            is_vase = cat == CAT_VASE or any(w in name.lower() for w in ("ваза", "кашпо", "кружка"))
            if is_vase:
                add = vase_additions(name, have)
            else:
                add = flower_additions(oid, name, cd, have_map)
            # описание
            new_desc = None
            desc = offer.get("description") or ""
            if not is_vase and len(desc) < 500:
                new_desc = (desc + DESC_EXT)[:5900]
            if add or new_desc:
                plan.append((oid, add, new_desc, rating))
        print(f"к дозаполнению: {len(plan)} карточек")
        from collections import Counter
        cnt = Counter()
        for _, add, nd, _ in plan:
            for a in add:
                cnt[a["parameterId"]] += 1
            if nd:
                cnt["описание"] += 1
        print("что добавляем:", dict(cnt))
        if not apply:
            print("dry-run. Применить: python upgrade_market_cards.py apply")
            return
        ok = err = 0
        for i in range(0, len(plan), 10):
            batch = []
            for oid, add, new_desc, _ in plan[i:i + 10]:
                offer = dict(offers[oid][0])
                for k in ("customsCommodityCode", "campaigns", "cardStatus", "mediaFiles"):
                    offer.pop(k, None)
                merged = list((cards.get(oid) or {}).get("parameterValues") or [])
                merged += add
                offer["parameterValues"] = merged
                if new_desc:
                    offer["description"] = new_desc
                batch.append({"offer": offer})
            r = await c.post(f"https://api.partner.market.yandex.ru/v2/businesses/{BID}/offer-mappings/update",
                             headers=H, json={"offerMappings": batch})
            d = r.json()
            bad = False
            for res in (d.get("result") or {}).get("results") or []:
                if res.get("errors"):
                    bad = True
                    err += 1
                    print("  ОШИБКА", res.get("offerId"), json.dumps(res["errors"], ensure_ascii=False)[:180])
            if not bad:
                ok += len(batch)
            await asyncio.sleep(1)
        print(f"обновлено: {ok}, с ошибками: {err}")


if __name__ == "__main__":
    asyncio.run(main(len(sys.argv) > 1 and sys.argv[1] == "apply"))
