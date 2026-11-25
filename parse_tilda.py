import json, re
paths = [
 r"C:/Users/наташа/.kimi-code/sessions/wd_kimi_21ffd0d4eb40/session_f1c3e4bf-f9cf-4bdd-bd0f-0272e88f15a8/agents/main/tool-results/mcp__tilda__store_get_products_list-tool_T8qlNRBO1rqumEl4HjGQyeji-be84f558-0b03-461b-a4b5-6e5767affedb.txt",
 r"C:/Users/наташа/.kimi-code/sessions/wd_kimi_21ffd0d4eb40/session_f1c3e4bf-f9cf-4bdd-bd0f-0272e88f15a8/agents/main/tool-results/mcp__tilda__store_get_products_list-tool_xDn4FluTQSdbPoWCH2ooh16x-8ab14988-ba89-4dee-9973-3e2cf3e408a1.txt",
]
prods = []
for p in paths:
    raw = open(p, encoding="utf-8").read()
    raw = raw.split("<mcp-result-extras>")[0].split("[Per-line truncation")[0]
    for m in re.finditer(r'\{"id":\d+,"uid":(\d+),', raw):
        uid = int(m.group(1))
        # окно до следующего товара или 4000 символов
        nxt = raw.find('{"id":', m.end())
        blob = raw[m.start(): nxt if nxt > 0 else m.start() + 4000]
        tm = re.search(r'"title":"(.*?)",', blob)
        om = re.search(r'"off":"(.*?)"', blob)
        pm = re.search(r'"price_min":"([\d.]+)"', blob) or re.search(r'"price":"([\d.]+)"', blob)
        px = re.search(r'"price_max":"([\d.]+)"', blob) or pm
        title = tm.group(1) if tm else ""
        try:
            title = json.loads('"' + title + '"')
        except Exception:
            pass
        prods.append({"uid": uid, "title": title, "off": om.group(1) if om else "",
                      "price_min": pm.group(1) if pm else "",
                      "price_max": px.group(1) if px else ""})
uniq = {p["uid"]: p for p in prods}
print("товаров Тильды:", len(uniq), "| с ценой:", sum(1 for p in uniq.values() if p["price_min"]))
json.dump(list(uniq.values()), open("tilda_products.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
