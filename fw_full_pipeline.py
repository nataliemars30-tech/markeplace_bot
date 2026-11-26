"""Единый конвейер Flowwow: map_offer -> состав (details) -> классический update.

Для каждого активного товара без артикула: присваиваем offerId (MCP), снимаем
реальный состав (MCP details), затем классическим API (отдельный лимит-бюджет!)
ставим подборки + 1 минуту + поля, сохраняя настоящий состав.
Для уже присвоенных (fw_map_progress.json) — только details + update.
Прогресс: fw_details.json (составы), fw_updated.json (обновлённые).
"""

import json
import re
import time
import urllib.request

import fw_mcp as M

THEMES = [1, 150, 48, 53, 40, 32, 39, 33, 34, 102, 159]
EXPENDABLES = ("упаковк", "тишью", "лента", "кризал", "аквабокс", "открытка", "коробка",
               "оазис", "бант", "подкормка", "транспортировочн")

sec = dict(l.strip().split("=", 1) for l in open(".secrets.local", encoding="utf-8") if "=" in l)
TOK, SID = sec["FLOWWOW_TOKEN"], sec["FLOWWOW_SHOP_ID"]


def classic_post(path, body, tries=5):
    for a in range(tries):
        try:
            r = urllib.request.Request("https://apis.flowwow.com/apiseller" + path,
                                       data=json.dumps(body).encode(), method="POST",
                                       headers={"Authorization": f"Bearer {TOK}",
                                                "Content-Type": "application/json"})
            with urllib.request.urlopen(r, timeout=60) as resp:
                return json.load(resp)
        except Exception as e:
            if a == tries - 1:
                raise
            time.sleep(8)
    return {}


def load_json(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def mcp_call(name, args, rid=1):
    """MCP-вызов с ожиданием 429."""
    while True:
        d = M.call_tool(name, args, rid=rid)
        txt = d.get("text") or json.dumps(d, ensure_ascii=False)
        m = re.search(r'"retry_after":\s*"?(\d+)', txt)
        if m:
            wait = int(m.group(1)) + 20
            print(f"  429: жду {wait}с", flush=True)
            time.sleep(wait)
            M.init()
            continue
        return d


COLORS = {"красн": 1, "розов": 2, "оранж": 3, "жёлт": 4, "желт": 4, "зелен": 5, "зелён": 5,
          "син": 7, "фиолет": 8, "черн": 9, "чёрн": 9, "бел": 10, "голуб": 12, "бордо": 13,
          "крем": 14, "сирен": 15, "коралл": 16, "беж": 20}


def color_of(name):
    low = (name or "").lower()
    for k, v in COLORS.items():
        if k in low:
            return v
    return 11


def main():
    items = load_json("fw_products_full.json", [])
    targets = [p for p in items if p.get("isActive") and not p.get("isArchived")]
    mapped = set(load_json("fw_map_progress.json", []))
    details = load_json("fw_details.json", {})
    updated = set(load_json("fw_updated.json", []))
    print(f"активных: {len(targets)} | с артикулом: {len(mapped)} | "
          f"составов снято: {len(details)} | обновлено: {len(updated)}", flush=True)
    M.init()
    ok_map = ok_upd = err = 0
    for i, p in enumerate(targets):
        pid = p["productId"]
        oid = (p.get("offerId") or "").strip() or f"REN-FW-{pid}"
        try:
            # 1. артикул (если ещё нет)
            if pid not in mapped and not (p.get("offerId") or "").strip():
                prep = mcp_call("prepare_product_change",
                                {"change": {"operation": "map_offer", "shop_id": M.SHOP_ID,
                                            "offers": [{"offerId": oid, "productId": pid}]}}, rid=100 + i)
                if prep.get("text") and "Error" in prep["text"]:
                    if "нельзя назначить" in prep["text"]:
                        print(f"[{i+1}] готовый товар, пропуск map: {pid}", flush=True)
                        mapped.add(pid)  # не пытаемся снова
                        json.dump(sorted(mapped), open("fw_map_progress.json", "w"))
                        continue
                    print(f"[{i+1}] PREP ERR {pid}: {prep['text'][:100]}", flush=True)
                    err += 1
                    continue
                cid = prep.get("change_id")
                appl = mcp_call("apply_product_change", {"change_id": cid, "confirmed": True}, rid=200 + i)
                if appl.get("text") and "Error" in appl["text"]:
                    print(f"[{i+1}] APPLY ERR {pid}: {appl['text'][:100]}", flush=True)
                    err += 1
                    continue
                mapped.add(pid)
                json.dump(sorted(mapped), open("fw_map_progress.json", "w"))
                ok_map += 1
                time.sleep(1)
            # 2. состав (MCP details)
            if str(pid) not in details:
                det = mcp_call("get_product_details", {"shop_id": M.SHOP_ID, "offer_id": oid}, rid=300 + i)
                prod = (det.get("product") or {})
                consist = ((prod.get("properties") or {}).get("consist")) or []
                details[str(pid)] = consist
                json.dump(details, open("fw_details.json", "w", encoding="utf-8"), ensure_ascii=False)
                time.sleep(1)
            # 3. классический update: подборки + 1 минута + поля (с реальным составом)
            if pid not in updated:
                raw_cons = details.get(str(pid)) or []
                consist = [{"name": c["name"], "quantity": int(c.get("quantity") or 1), "measure": 1}
                           for c in raw_cons
                           if c.get("name") and not any(e in c["name"].lower() for e in EXPENDABLES)]
                if not consist:
                    consist = [{"name": "Цветы сезонные", "quantity": 1, "measure": 1}]
                item = {
                    "offerId": oid,
                    "productType": p.get("type") or 1,
                    "subCategoryId": p.get("subCategoryId") or 2,
                    "name": p["name"],
                    "price": str(int(float(p["price"]))),
                    "discount": str(p.get("discount") or "0"),
                    "description": p.get("description") or "",
                    "images": p.get("images") or [],
                    "productionTime": {"val": 1, "measure": 7},
                    "minOrderCount": p.get("minOrder") or 1,
                    "thematicSelections": THEMES,
                    "width": {"val": 35, "measure": 3},
                    "height": {"val": 45, "measure": 3},
                    "manufacturerName": "REN, Россия",
                    "manufacturerCountry": 1,
                    "color": color_of(p["name"]),
                    "consist": consist,
                }
                d = classic_post(f"/products/update?shopId={SID}", {"products": [item]})
                summ = d.get("summary") or {}
                if summ.get("success"):
                    updated.add(pid)
                    json.dump(sorted(updated), open("fw_updated.json", "w"))
                    ok_upd += 1
                else:
                    msgs = [e.get("message") for e in (d.get("errors") or [])]
                    print(f"[{i+1}] UPD ERR {pid}: {json.dumps(msgs, ensure_ascii=False)[:120]}", flush=True)
                    err += 1
                time.sleep(1)
            if (i + 1) % 10 == 0:
                print(f"прогресс: {i+1}/{len(targets)} | map+{ok_map} upd+{ok_upd} err {err}", flush=True)
        except Exception as e:
            print(f"СБОЙ {pid}: {str(e)[:80]} — пауза 60с", flush=True)
            err += 1
            time.sleep(60)
            try:
                M.init()
            except Exception:
                time.sleep(60)
    print(f"ГОТОВО: map+{ok_map}, update+{ok_upd}, ошибок {err}, "
          f"всего обновлено {len(updated)}/{len(targets)}", flush=True)


if __name__ == "__main__":
    main()
