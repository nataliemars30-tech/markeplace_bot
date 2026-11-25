"""Разовый XLSX прайс-лист для Яндекс Бизнеса (шаблон ЛК) с ссылками на Тильду."""
import json, re
import openpyxl
import yandex_feed as Y

fw = json.load(open("fw_products_full.json", encoding="utf-8"))
tilda = json.load(open("tilda_products.json", encoding="utf-8"))

def core(name: str) -> str:
    n = Y.EMOJI_RX.sub("", (name or "").lower())
    n = re.sub(r"[«\"'](.*?)[»\"']", r" \1 ", n)
    n = re.sub(r"[^a-zа-я0-9 ]", " ", n)
    for w in ("букет", "монобукет", "авторский", "нежный", "светлый", "осенний", "размер", "шт"):
        n = n.replace(w, " ")
    return re.sub(r"\s{2,}", " ", n).strip()

def quoted(name: str):
    m = re.search(r"[«\"]([^»\"]{3,40})[»\"]", name or "")
    return m.group(1).strip().lower() if m else None

# индекс Тильды
t_by_q = {}
for t in tilda:
    q = quoted(t["title"])
    if q and not t["off"]:
        t_by_q.setdefault(q, t)

def find_tilda(fw_name: str, maps_price: int):
    q = quoted(fw_name)
    if q and q in t_by_q:
        return t_by_q[q]
    fc = core(fw_name)
    best = None
    for t in tilda:
        if t["off"]:
            continue
        try:
            pmin, pmax = float(t["price_min"] or 0), float(t["price_max"] or t["price_min"] or 0)
        except ValueError:
            continue
        if not (pmin <= maps_price <= pmax):
            continue
        tc = core(t["title"])
        if tc and (tc in fc or fc in tc):
            if best is None or len(tc) > len(core(best["title"])):
                best = t
    return best

wb = openpyxl.Workbook()
ws = wb.active
ws.append(["Категория", "Название", "Идентификатор", "Описание", "Короткое описание",
           "Цена", "Ссылка", "Фотография", "Популярный товар", "В наличии",
           "Количество", "Единицы измерения"])
CATN = {"1": "Авторские букеты", "2": "Монобукеты", "3": "Цветы поштучно",
        "4": "Вазы и кашпо", "5": "Композиции и декор"}
n = matched = popular = 0
for p in fw:
    if not p.get("isActive") or p.get("isArchived"):
        continue
    price = Y._maps_price(float(p.get("price") or 0))
    name = Y._clean(p.get("name") or "")[:250]
    if price <= 0 or not name:
        continue
    cat = Y._category(p)
    desc = Y._seo_description(p, cat)
    short = Y._clean(desc.split("\n\n")[0])[:250]
    imgs = [u for u in (p.get("images") or []) if u.startswith("http")]
    t = find_tilda(p.get("name") or "", price)
    link = f"https://ren-flowers.ru/tproduct/{t['uid']}" if t else ""
    matched += bool(t)
    is_pop = ""
    if p.get("isStarred") and popular < 10:
        is_pop = "Да"; popular += 1
    ws.append([CATN[cat], name, f"FW-{p.get('productId')}", desc, short, price,
               link, imgs[0] if imgs else "", is_pop, "", 1, "штука"])
    n += 1
wb.save(r"C:\Users\наташа\Desktop\yandex_business_pricelist.xlsx")
print(f"строк: {n} | со ссылкой на Тильду: {matched} | популярных: {popular}")
