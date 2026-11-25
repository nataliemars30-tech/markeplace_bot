"""Апгрейд карточек букетов на Tilda: табы (опечатка, «Фото» v2, +«Доставка и оплата»),
5 опций-допов с галочками (вкл. кросс-сейл ваз), нейтральные характеристики (без состава —
сезонная подмена, решение владельца 25.11). Прогресс: tilda_bouquet_progress.json."""

import asyncio
import json
import sys
import types as T

sys.path.insert(0, ".")
m = T.ModuleType("config")
m.config = T.SimpleNamespace(openai_api_key="", tilda_mcp_token="", tilda_project_id=13756635)
sys.modules["config"] = m
import tilda_mcp as TM  # noqa: E402

_mcp = json.load(open(r"C:/Users/наташа/.kimi-code/mcp.json", encoding="utf-8"))
TOKEN = _mcp["mcpServers"]["tilda"]["headers"]["Authorization"].replace("Bearer ", "").strip()
PID = 13756635
PROGRESS = "tilda_bouquet_progress.json"

CARE = ("Несколько простых шагов помогут сохранить красоту цветов надолго:<br /><br />"
        "1. Освободите цветы от упаковки - так они вздохнут с облегчением.<br />"
        "2. Наполните чистую вазу как минимум на половину прохладной водой, используйте 1/2 пакета "
        "подаренного удобрения (кол-во воды зависит от высоты вашей вазы).<br />"
        "3. Удалите листья, которые могут оказаться ниже уровня воды.<br />"
        "4. Подрежьте стебли под углом 45 градусов острым ножом или секатором и в течение 20 секунд "
        "поставьте стебли в воду.<br />"
        "5. Поддерживайте свежесть: меняйте воду и обновляйте срез раз в 2-3 дня, не допускайте "
        "обезвоживания цветов.<br /><br />"
        "Помните, что цветы любят прохладу: держите подальше от отопительных приборов, сквозняков "
        "и открытых продуктов. Со временем удаляйте завядшую листву и бутоны — так цветок будет "
        "лучше напитываться водой.")
UNIQ = ("В природе каждый цветок уникален и не повторим, поэтому его оттенок и размер могут слегка "
        "меняться от поставки к поставке. В связи с этим, ваш букет может незначительно отличаться "
        "от фото букета в каталоге.<br /><br />"
        "Наши флористы сохранят настроение и цветовую гамму каждого букета, опираясь на имеющийся "
        "в студии выбор цветов - подберут лучший вариант!<br />"
        "Мы обещаем, что ни одна замена цветка в составе не испортит красоты букета!")
PHOTO = ("Фото готового букета перед отправкой — обязательная часть нашей работы. Вы согласовываете "
         "букет до того, как курьер выедет, и точно знаете, что получит адресат.")
DELIVERY = ("Доставка по Санкт-Петербургу — за 90 минут в выбранный интервал. Стоимость зависит от "
            "зоны: чем ближе к студии, тем выгоднее.<br /><br />"
            "Самовывоз из студии — бесплатно, букет будет готов к вашему приезду.<br />"
            "Оплата — картой на сайте при оформлении заказа.<br /><br />"
            "Перед отправкой пришлём фото готового букета для согласования.")

# 5 опций: (название, [(значение, цена)])
OPTIONS = [
    ("Открытка", [("Без открытки", "0"), ("Фирменная открытка", "150"),
                  ("«Сердце — как друга море встречает»", "180"),
                  ("«У меня к тебе нежность»", "180")]),
    ("Транспортировочный набор", [("Не нужен", "0"), ("Добавить (коробка и аквабокс)", "490")]),
    ("Секатор", [("Не нужен", "0"), ("Добавить секатор", "900")]),
    ("Подкормка для цветов", [("Не нужна", "0"), ("Добавить подкормку", "100")]),
    ("Ваза к букету", [("Без вазы", "0"), ("Ваза «Хлоя» 11×20", "1960"),
                       ("Ваза «Колонна» 10×16,5", "1640"), ("Ваза «Банка» тёмная 16×25", "1390")]),
]

DECOR_WORDS = ("ваза", "кашпо", "открытка", "растение", "монстера", "драцена", "набор", "секатор",
               "подкормка", "кружка")


def load_progress():
    try:
        return set(json.load(open(PROGRESS)))
    except Exception:
        return set()


async def fetch_all_products(cl):
    out, slice_ = [], 1
    while slice_ <= 6:
        raw = await cl.call_tool("store_get_products_list",
                                 {"projectid": PID, "slice": slice_, "slicesize": 100})
        d = json.loads(raw)
        batch = d.get("products") or []
        if not batch:
            break
        out += batch
        if len(batch) < 100:
            break
        slice_ += 1
    return out


async def main():
    cl = TM.TildaMCPClient(token=TOKEN)
    products = await fetch_all_products(cl)
    bouquets = [p for p in products
                if not any(w in (p.get("title") or "").lower() for w in DECOR_WORDS)
                and not str(p.get("sku") or "").startswith(("VASE-", "PLANT-", "CARD-"))]
    print(f"букетов к апгрейду: {len(bouquets)}", flush=True)
    done = load_progress()
    ok = err = 0
    for i, p in enumerate(bouquets):
        uid = p.get("uid")
        if uid in done:
            continue
        args = {
            "projectid": PID, "productuid": uid, "title": p.get("title") or "Букет",
            # табы: правильные названия, «Фото» v2, + доставка (columnar-формат)
            "tabs": {
                "title": ["Инструкция по уходу", "Об уникальности каждого цветка",
                          "Фото перед отправкой", "Доставка и оплата"],
                "type": ["text", "text", "text", "text"],
                "data": [CARE, UNIQ, PHOTO, DELIVERY],
            },
            "characteristics": {
                "title": ["Высота", "Оформление", "Сборка"],
                "value": ["45 см", "фирменная упаковка студии REN", "флористом в день доставки"],
            },
        }
        for n, (opt_title, variants) in enumerate(OPTIONS, start=1):
            suffix = "" if n == 1 else str(n)
            args[f"prod_option{suffix}"] = opt_title
            args[f"prod_variants{suffix}"] = json.dumps(
                [{"value": v, "price": pr} for v, pr in variants], ensure_ascii=False)
        for attempt in range(3):
            try:
                await cl.call_tool("store_save_products", args)
                ok += 1
                done.add(uid)
                json.dump(sorted(done), open(PROGRESS, "w"))
                break
            except Exception as e:
                if "429" in str(e) and attempt < 2:
                    await asyncio.sleep(20)
                    continue
                print(f"[{i+1}] ERR {(p.get('title') or '')[:40]}: {str(e)[:80]}", flush=True)
                err += 1
                break
        if (i + 1) % 20 == 0:
            print(f"прогресс: {ok} ок, {err} ошибок ({i+1}/{len(bouquets)})", flush=True)
        await asyncio.sleep(1.2)
    print(f"ГОТОВО: обновлено {ok}, ошибок {err}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
