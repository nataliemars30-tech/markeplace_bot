"""Массовое присвоение артикулов (map_offer) старым товарам Flowwow через MCP.

После присвоения offerId товары редактируются классическим API products/update.
Прогресс пишется в fw_map_progress.json — при прерывании продолжает с места.
"""

import json
import re
import time

import fw_mcp as M

PROGRESS = "fw_map_progress.json"


def load_progress():
    try:
        return set(json.load(open(PROGRESS)))
    except Exception:
        return set()


def save_progress(done):
    json.dump(sorted(done), open(PROGRESS, "w"))


def main():
    items = json.load(open("fw_products_full.json", encoding="utf-8"))
    targets = [p for p in items
               if p.get("isActive") and not p.get("isArchived") and not (p.get("offerId") or "").strip()]
    print(f"к присвоению артикулов: {len(targets)}", flush=True)
    done = load_progress()
    M.init()
    ok = err = 0
    for i, p in enumerate(targets):
        pid = p["productId"]
        if pid in done:
            continue
        oid = f"REN-FW-{pid}"
        while True:
            prep = M.call_tool("prepare_product_change", {"change": {
                "operation": "map_offer", "shop_id": M.SHOP_ID,
                "offers": [{"offerId": oid, "productId": pid}]}}, rid=100 + i)
            txt = prep.get("text") or json.dumps(prep, ensure_ascii=False)
            m = re.search(r'"retry_after":\s*"?(\d+)', txt)
            if m:
                wait = int(m.group(1)) + 30
                print(f"429: жду {wait}с (товар {i+1}/{len(targets)})", flush=True)
                time.sleep(wait)
                M.init()
                continue
            break
        if prep.get("text") and "Error" in prep["text"]:
            print("PREP ERR", pid, prep["text"][:150], flush=True)
            err += 1
            time.sleep(2)
            continue
        # подтверждаем
        proposal_id = prep.get("proposal_id") or prep.get("id") or (prep.get("proposal") or {}).get("id")
        args = {"proposal_id": proposal_id} if proposal_id else {"change": {"operation": "map_offer",
                "shop_id": M.SHOP_ID, "offers": [{"offerId": oid, "productId": pid}]}}
        while True:
            appl = M.call_tool("apply_product_change", args, rid=200 + i)
            txt = appl.get("text") or json.dumps(appl, ensure_ascii=False)
            m = re.search(r'"retry_after":\s*"?(\d+)', txt)
            if m:
                wait = int(m.group(1)) + 30
                print(f"429 (apply): жду {wait}с", flush=True)
                time.sleep(wait)
                M.init()
                continue
            break
        if appl.get("text") and "Error" in appl["text"]:
            print("APPLY ERR", pid, appl["text"][:150], flush=True)
            err += 1
        else:
            ok += 1
            done.add(pid)
            save_progress(done)
            if ok % 10 == 0:
                print(f"прогресс: {ok} ок, {err} ошибок ({i+1}/{len(targets)})", flush=True)
        time.sleep(1.5)
    print(f"ГОТОВО: присвоено {ok}, ошибок {err}", flush=True)


if __name__ == "__main__":
    main()
