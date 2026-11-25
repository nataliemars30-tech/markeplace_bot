"""Правка экспорта Еды: моно-названия (Монобукет + N + цвет + цветок + сорт RU/EN),
реальные количества в составе моно, состав авторских из MCP (если доступен)."""
import csv, json, re, sys, types as T
sys.modules.setdefault("config", T.ModuleType("config"))
sys.modules["config"].config = T.SimpleNamespace(openai_api_key="")
import bulk_eda as B

COLOR_ROD = {"оранжев": "оранжевых", "бел": "белых", "красн": "красных", "розов": "розовых",
             "желт": "жёлтых", "жёлт": "жёлтых", "крем": "кремовых", "персик": "персиковых",
             "голуб": "голубых", "сирен": "сиреневых", "зелен": "зелёных", "зелён": "зелёных",
             "бордо": "бордовых", "бежев": "бежевых", "син": "синих", "фиолет": "фиолетовых",
             "алы": "алых", "разноцвет": "разноцветных"}

def parse_mono(name):
    """-> (qty|None, color|None, flower_dict|None, sort|None)"""
    n = name
    qty = None
    m = re.search(r"\b(\d{1,3})\s*(?:шт|штук)", n.lower()) or re.search(r"\bиз\s+(\d{1,3})\b", n.lower())
    if m:
        qty = int(m.group(1))
    color = None
    for k, v in COLOR_ROD.items():
        if k in n.lower():
            color = v
            break
    comp = B.eda_component(n)  # цветок по справочнику
    # сорт: латинские слова или (Русское) в скобках, кроме служебных
    sort = None
    sm = re.findall(r"\b([A-Z][a-zA-Z]{2,}(?:\s[A-Z][a-zA-Z]{2,})?)\b", n)
    stop = {"REN", "TO", "GO", "XL", "M", "L", "S", "Mix", "The"}
    lat = [w for w in sm if w not in stop]
    rm = re.search(r"\(\s*([А-Яа-яA-Za-z ]{3,25})\s*\)", n)
    rus = rm.group(1).strip() if rm else None
    if lat:
        sort = lat[0] + (f" ({rus})" if rus and rus.lower() not in lat[0].lower() else "")
    elif rus:
        sort = rus
    else:
        # русский сорт без скобок: «хризантем Бигуди Ред», «розы Кахала»
        STOP_RU = ("моно", "монобукет", "букет", "из", "с", "и", "в", "крафте", "под", "ленту",
                   "штук", "шт", "оттенка", "веточкой", "эвкалиптом", "эвкалипта",
                   "ароматной", "ароматный", "осенних", "осенний", "весенний", "огромная",
                   "корзина", "цвет", "можно", "согласовать", "заранее")
        for k in COLOR_ROD:
            STOP_RU += (k,)
        toks = n.replace("(", " ").replace(")", " ").split()
        cand = []
        for t in toks:
            tl = t.lower().strip(",.«»\"")
            if t[:1].isupper() and tl not in STOP_RU and not any(tl.startswith(k) for k in COLOR_ROD):
                if B.eda_component(tl) is None:  # не название цветка
                    cand.append(t.strip(",."))
        if cand:
            sort = " ".join(cand[:2])
    return qty, color, comp, sort

def mono_name(qty, color, comp, sort, old_name):
    if not comp:
        return old_name  # не смогли определить цветок — не трогаем
    plural = B.PLURAL_GEN.get(comp)
    if not plural and len(comp.split()) == 1:
        plural = B._plural_gen(comp)
    if not plural:
        return old_name
    parts = ["Монобукет из"]
    if qty:
        parts.append(str(qty))
    if color:
        parts.append(color)
    parts.append(plural)
    if sort:
        parts.append(sort)
    return " ".join(parts)[:250]

def main():
    rows = json.load(open("eda_export2.json", encoding="utf-8"))
    hdr_src, data = rows[0], rows[1:]
    # в официальный импорт-шаблон (запятая), id позиции сохраняем
    out_rows = []
    renamed = qty_fixed = 0
    report = []
    for r in data:
        cat, name, pid = r.get("A",""), r.get("G",""), r.get("H","")
        consist = r.get("AA","") or ""
        if cat == "Монобукеты":
            qty, color, comp, sort = parse_mono(name)
            new_name = mono_name(qty, color, comp, sort, name)
            if new_name != name:
                renamed += 1
            # количество в составе
            if qty and consist:
                parts = consist.split(",")
                if len(parts) == 1 and ":" in parts[0]:
                    fl = parts[0].rsplit(":", 1)[0]
                    if parts[0].rsplit(":", 1)[1].strip() != str(qty):
                        consist = f"{fl}:{qty}"
                        qty_fixed += 1
            name = new_name
        row = [r.get(c, "") for c in ("A","B","C","D","E","F","G","H","I","J","K","L","M","N","O",
                                      "P","Q","R","S","T","U","V","W","X","Y","Z","AA")]
        gi = 6
        row[gi] = name
        row[26] = consist
        out_rows.append(row)
    with open("eda_fixed.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(B.TEMPLATE_HEADERS)
        w.writerows(out_rows)
    print(f"строк: {len(out_rows)} | моно переименовано: {renamed} | количество исправлено: {qty_fixed}")
    # примеры
    shown = 0
    for r in out_rows:
        if r[0] == "Монобукеты" and shown < 12:
            print(" -", r[6][:70], "|", r[26][:30])
            shown += 1

if __name__ == "__main__":
    main()
