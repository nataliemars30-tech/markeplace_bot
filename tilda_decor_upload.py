"""Загрузка декора (вазы/кашпо) и комнатных растений с Flowwow в каталог Tilda.

- Берём АКТИВНЫЕ на Flowwow; дедуп по имени: основной товар (type=1) с макс. фото.
- Пропускаем то, что уже есть на Тильде (матч по ядру названия).
- Цена Tilda = base x 1.1 (до десятков). Рубрика: «Декор».
- Прогресс: tilda_upload_progress.json (продолжает с места после обрыва).
"""

import asyncio
import json
import re
import sys
import types as T

sys.path.insert(0, ".")
m = T.ModuleType("config")
m.config = T.SimpleNamespace(openai_api_key="", tilda_mcp_token="", tilda_project_id=13756635)
sys.modules["config"] = m

_mcp = json.load(open(r"C:/Users/наташа/.kimi-code/mcp.json", encoding="utf-8"))
TOKEN = _mcp["mcpServers"]["tilda"]["headers"]["Authorization"].replace("Bearer ", "").strip()
PROJECT_ID = 13756635

import tilda_mcp as TM  # noqa: E402
from sync_market_decor import norm_key  # noqa: E402
from yandex_feed import _clean, EMOJI_RX  # noqa: E402

PROGRESS = "tilda_upload_progress.json"
PLANT_WORDS = ("монстера", "драцена", "пальма", "фикус", "стефанотис", "жасмин",
               "сингониум", "непентес", "мухоловка", "антуриум", "растение")


def load_progress():
    try:
        return set(json.load(open(PROGRESS)))
    except Exception:
        return set()


def core(name):
    n = EMOJI_RX.sub("", (name or "").lower())
    n = re.sub(r"[«\"']", " ", n)
    n = re.sub(r"[^a-zа-я0-9 ]", " ", n)
    for w in ("ваза", "кашпо", "ren", "стеклянная", "керамическая", "стекло", "керамика",
              "см", "настольная", "для", "букетов", "и", "интерьера", "дизайнерская"):
        n = re.sub(rf"\b{w}\b", " ", n)
    return re.sub(r"\s{2,}", " ", n).strip()


async def main():
    full = json.load(open("fw_products_full.json", encoding="utf-8"))
    types = {p["productId"]: p.get("type") for p in full}
    decor_src = json.load(open("fw_decor_full.json", encoding="utf-8"))
    tilda = json.load(open("tilda_products.json", encoding="utf-8"))
    tilda_cores = [core(t.get("title")) for t in tilda if not t.get("off")]

    # активные вазы/кашпо, дедуп: type=1, макс фото
    groups = {}
    for p in decor_src:
        if p["active"]:
            groups.setdefault(norm_key(p["name"]), []).append(p)
    targets = []
    for k, items in groups.items():
        best = sorted(items, key=lambda x: (0 if types.get(x["id"]) == 1 else 1,
                                            -len(x.get("images") or []),
                                            float(x["price"])))[0]
        targets.append(best)
    # растения (активные)
    for p in full:
        if p.get("isActive") and not p.get("isArchived") and \
           any(w in (p.get("name") or "").lower() for w in PLANT_WORDS) and \
           "букет" not in (p.get("name") or "").lower():
            targets.append({"id": p["productId"], "name": p["name"], "price": p["price"],
                            "active": True, "images": p.get("images") or [],
                            "desc_full": p.get("description") or ""})
    print(f"к загрузке: {len(targets)} позиций", flush=True)

    client = TM.TildaMCPClient(token=TOKEN)
    cat = TM.TildaCatalog()
    cat.client = client
    cat._available = True

    done = load_progress()
    ok = skip = err = 0
    for i, p in enumerate(targets):
        pid = p["id"]
        if pid in done:
            continue
        name = _clean(p["name"])[:190]
        c = core(name)
        if c and any(c and (c in tc or tc in c) and len(tc) > 4 for tc in tilda_cores):
            print(f"[{i+1}] пропуск (есть на Тильде): {name[:50]}", flush=True)
            done.add(pid)
            json.dump(sorted(done), open(PROGRESS, "w"))
            skip += 1
            continue
        price = int(round((float(p["price"]) / 1.5 - 150) * 1.1 / 10) * 10)
        if price <= 0:
            continue
        is_plant = any(w in name.lower() for w in PLANT_WORDS)
        kind = "Комнатное растение" if is_plant else ("Кашпо" if "кашпо" in name.lower() else "Ваза")
        desc = (f"{kind} от цветочной студии REN (Санкт-Петербург). Лаконичная форма и азиатская "
                f"эстетика: подходит для живых букетов, сухоцветов и как самостоятельный "
                f"интерьерный объект. Самовывоз из студии или бережная доставка по СПб.")
        try:
            res = await cat.create_product(
                project_id=PROJECT_ID,
                title=name,
                price_tilda=price,
                photo_urls=[u for u in (p.get("images") or []) if u.startswith("http")][:6],
                tilda_categories=["Декор"],
                description=desc,
                full_article=f"{'PLANT' if is_plant else 'VASE'}-{pid}",
                seo_title=f"{name} — купить в Санкт-Петербурге | REN"[:200],
                seo_descr=desc[:400],
                seo_keywords=f"{kind.lower()}, купить вазу спб, декор для букетов, цветочная студия REN",
                height_cm=25, weight_g=1000, width_cm=15)
            print(f"[{i+1}] {name[:45]} | {price}₽ -> {res[:60]}", flush=True)
            done.add(pid)
            json.dump(sorted(done), open(PROGRESS, "w"))
            ok += 1
        except Exception as e:
            print(f"[{i+1}] ОШИБКА {name[:40]}: {str(e)[:100]}", flush=True)
            err += 1
        await asyncio.sleep(3)
    print(f"ГОТОВО: создано {ok}, пропущено {skip}, ошибок {err}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
