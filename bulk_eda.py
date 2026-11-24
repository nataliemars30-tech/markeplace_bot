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

from eda_csv import CAT_IDS, PLACES, _plural_gen
from platform_rules import clean_position_name


# Официальный шаблон импорта PickerApp (cvetImport.csv от поддержки, 24.11):
# разделитель ЗАПЯТАЯ, UTF-8 БЕЗ BOM, заголовки короткие (без многострочных примечаний).
# Наш прежний формат (точка с запятой + заголовки из выгрузки ЛК) импортер отклоняет
# с «Invalid request» — это формат ЭКСПОРТА, а не импорта.
TEMPLATE_HEADERS = [
    "Название категории 1 уровень", "id категории 1 уровень",
    "Название категории 2 уровень", "id категории 2 уровень",
    "Название категории 3 уровень", "id категории 3 уровень",
    "Название позиции", "id позиции", "Общий вес позиции", "Единица измерения",
    "Квантовка", "Цена", "Описание товара", "Производитель", "Страна", "Штрихкоды",
    "Товар доступен в магазинах", "Акциза", "Ссылки на картинки",
    "Налоговые коды товаров (для Узбекистана)", "Налоговые коды упаковок (для Узбекистана)",
    "Требуется маркировка (для Узбекистана)", "Товар для взрослых",
    "Кол-во товаров в упаковке", "Ширина (см)", "Высота (см)",
    "Состав букета (в формате: Наименование позиции:кол-во,Наименование позиции:кол-во,"
    "...и т.д.. Без пробелов. Например Роза пионовидная:10,Ромашка:5,... и т.д..) "
    "Наименование позиции нужно брать из справочника",
]

# ------------------ Официальный справочник компонентов Еды (disk.yandex.ru/i/TlySd-e20yV0Qw, 24.11) ------------------
EDA_DICT = [s.strip() for s in """
Агапантус|Аллиум|Алоэ|Альстромерия|Амарант|Амарилис|Амми|Анемон|Анигозантос|Антирринум|Антуриум|Артишок|Асклепиас|Аспарагус|Аспидистра|Астер|Астильба|Астра|Астранция|Банксия|Бархатец|Берграсс|Береза|Брассика|Бруния|Бувардия|Буплерум|Василёк|Вейник|Верба|Вереск|Вероника|Ветка бука|Ветка дуба|Ветка малины|Вибирнум|Ворсянка|Гардения|Гвоздика|Геликония|Гелихризум|Гениста|Гентиана|Георгин|Гербера|Гиацинт|Гибискус|Гиперикум|Гиппеаструм|Гипсофила|Гладиолус|Глориоза|Гомфрена|Гортензия|Гревиллея|Дельфиниум|Диантус|Драцена бамбук|Житняк|Зелёные ветки. Зелень и декор|Зелень фисташки|Илекс|Ирис|Калла|Камыш|Канатник|Картамус|Кверкус|Клематис|Книфофия|Ковыль|Колокольчик|Корилус|Космея|Костер безостый|Котинус|Краспедия|Крокосмия|Крокус|Куркума|Лабазник|Лаванда|Лавандин|Лагурус|Ландыш|Латирус|Лен|Леувенбек|Леукадендрон|Леукоспермум|Лизиантус|Лилейник|Лилия|Лимониум|Лисохвост|Лотос|Лунария|Люпин|Лютик|Магнолия|Мак|Манжетка|Маттиола|Мережка|Мимоза|Мискантус|Можжевельник|Молочай|Молюцелла|Мускари|Мятлик|Нарцисс|Незабудка|Нерине|Нигелла|Нобилис|Нутан|Овес|Овсяница|Одуванчик|Озотамнус|Оксипеталум|Олеандр|Орнитогалум|Орхидея|Осот|Очиток|Пальма|Пампасная трава|Паникум|Папоротник|Пеннисетум|Пижма|Пион|Пираканта|Питтоспорум|Подснежник|Подсолнух|Протея|Пузыреплодник|Пшеница|Ранункулюс|Рогоз|Рожь|Роза кустовая|Роза одноголовая|Роза пионовидная|Роза французская|Ромашка|Рускус|Салал|Самшит|Сангисорба|Сандерсония|Серрурия|Сетария|Симфорикарпус|Синкарфа|Сирень|Ситник|Скабиоза|Скиммия|Солерос|Солидаго|Сорго|Спирея|Статица|Стрелиция|Сурепка|Твидия|Тимофеевка|Тласпи|Трахелиум|Тростник|Тубероза|Туя|Тысячелистник|Тюльпан|Фалярис|Фиалка|Физалис|Физостегия|Флокс|Флум|Форзиция|Фрезия|Фритиллярия|Хамелациум|Хасмантиум|Хелеборус (Морозник)|Хеномелис|Хлопок|Хризантема|Хризантема кустовая|Хризантема одноголовая|Целозия|Цинния|Чозия|Шиповник|Эвкалипт|Эремурус|Эрика|Эрингиум|Эхинацея|Эхинопсис|Ятрофа
""".replace("\n", "").split("|") if s.strip()]

# Наши названия -> точная строка справочника (там, где прямого совпадения нет)
EDA_ALIASES = {
    "эустом": "Лизиантус", "лизиантус": "Лизиантус",
    "амариллис": "Амарилис", "анемон": "Анемон",
    "писташ": "Зелень фисташки", "зелен": "Зелёные ветки. Зелень и декор",
    "листья дуба": "Ветка дуба", "дуб": "Ветка дуба",
    "статиц": "Статица", "лаванда": "Лаванда",
    "антир": "Антирринум",  # частая опечатка (одна «р»)
    "нитан": "Нутан", "нутан": "Нутан",
}


def eda_component(raw_name: str):
    """Название позиции/кусок названия товара -> ТОЧНАЯ строка справочника Еды или None."""
    low = str(raw_name).lower().replace("ё", "е")
    ws = [w.strip(",.()«»\"'—-") for w in low.split()]
    # розы: в справочнике только с уточнением (кустовая/одноголовая/пионовидная/французская);
    # «розовый» (цвет) — НЕ роза
    if any(w == "роз" or (w.startswith("роз") and not w.startswith("розов")) for w in ws):
        if "пионовидн" in low:
            return "Роза пионовидная"
        if "французск" in low:
            return "Роза французская"
        if "кустов" in low:
            return "Роза кустовая"
        return "Роза одноголовая"
    if "хризантем" in low:
        if "кустов" in low:
            return "Хризантема кустовая"
        if "одноголов" in low:
            return "Хризантема одноголовая"
        return "Хризантема"
    for alias, target in EDA_ALIASES.items():
        if alias in low:
            return target
    # прямой матч по справочнику: основа первого слова против слов текста.
    # Длинные названия первыми — иначе «астранция» сматчится в «Астру»
    words = [w.strip(",.()«»\"'—-") for w in low.split() if w.strip(",.()«»\"'—-")]
    for entry in sorted(EDA_DICT, key=len, reverse=True):
        e = entry.lower().replace("ё", "е")
        if e in low:
            return entry
        first = e.split()[0]
        stem = first[:-1] if first.endswith(("а", "я")) else first
        if len(stem) >= 4 and any(w.startswith(stem) for w in words):
            return entry
    return None


def to_eda_dpos(raw_pos):
    """[(имя, qty)] -> [(компонент справочника, qty)] с дедупом и суммированием."""
    out, order = {}, []
    for nm, q in raw_pos:
        comp = eda_component(nm)
        if not comp:
            continue
        if comp not in out:
            order.append(comp)
            out[comp] = 0
        out[comp] += max(1, int(q))
    return [(c, out[c]) for c in order]


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

# Родительный падеж мн.ч. для названий монобукетов («Букет из 25 …»)
PLURAL_GEN = {
    "Роза одноголовая": "роз", "Роза кустовая": "кустовых роз",
    "Роза пионовидная": "пионовидных роз", "Роза французская": "французских роз",
    "Хризантема": "хризантем", "Хризантема кустовая": "кустовых хризантем",
    "Хризантема одноголовая": "хризантем", "Тюльпан": "тюльпанов",
    "Гвоздика": "гвоздик", "Пион": "пионов", "Гортензия": "гортензий",
    "Лизиантус": "эустом", "Маттиола": "маттиол", "Ранункулюс": "ранункулюсов",
    "Анемон": "анемон", "Альстромерия": "альстромерий", "Гербера": "герб",
    "Ирис": "ирисов", "Калла": "калл", "Лилия": "лилий", "Нарцисс": "нарциссов",
    "Фрезия": "фрезий", "Гиацинт": "гиацинтов", "Орхидея": "орхидей",
    "Подсолнух": "подсолнухов", "Гладиолус": "гладиолусов", "Астра": "астр",
    "Георгин": "георгин", "Мимоза": "веток мимозы", "Лаванда": "веточек лаванды",
    "Орнитогалум": "орнитогалумов", "Незабудка": "незабудок", "Твидия": "твидий",
    "Оксипеталум": "оксипеталумов", "Нерине": "нерине", "Ромашка": "ромашек",
    "Лютик": "лютиков", "Амариллис": "амариллисов", "Амарилис": "амариллисов",
    "Мускари": "мускари", "Подснежник": "подснежников", "Ландыш": "ландышей",
}


def mono_title(qty: int, comp: str, orig_name: str) -> str:
    """«Букет из 25 роз»; для компонентов без формы мн.ч. — исходное название."""
    pg = PLURAL_GEN.get(comp)
    if not pg and len(comp.split()) == 1:
        pg = _plural_gen(comp)
    if pg:
        return f"Букет из {qty} {pg}"[:100]
    return orig_name[:100]


# ------------------ Очистка под модерацию Яндекс Еды (24.11: все товары отклонены) ------------------
# Модерация Еды запрещает: упоминание услуг вне сервиса (самовывоз, фото перед отправкой),
# стоимость/скидки/акции, время и стоимость доставки.
EDA_FOOTER_MARKERS = ("REN - азиатская эстетика", "REN – азиатская", "REN — азиатская",
                      "Почему клиенты выбирают нашу студию", "Добавьте нас в избранное")
EDA_BANNED = ("бесплатн", "самовывоз", "доставк", "стоимост", "скидк", "акци", "избранн",
              "выгодн", "компенсац", "минут", "часов", "открытк", "подкормк", "фото букета",
              "подарок", "цене", "цену", "цены")
EMOJI_RX = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️←-⇿⌀-⏿\U0001F1E6-\U0001F1FF\u200d]")


def clean_eda_description(desc: str, title: str) -> str:
    d = desc or ""
    for m in EDA_FOOTER_MARKERS:
        i = d.find(m)
        if i > 0:
            d = d[:i]
    parts = re.split(r"(?<=[.!?])\s+|\n+", d)
    keep = []
    for s in parts:
        s = EMOJI_RX.sub("", s).strip()
        if s and not any(b in s.lower() for b in EDA_BANNED):
            keep.append(s)
    out = "\n\n".join(keep).strip()
    if len(out) < 100:
        out = (f"{title} от цветочной студии REN. Авторская флористика в азиатской эстетике: "
               f"свежие цветы утренней поставки, бережная сборка и фирменная упаковка.")
    return out[:3000]


def clean_eda_title(t: str) -> str:
    return re.sub(r"\s{2,}", " ", EMOJI_RX.sub("", t or "")).strip(" ,-–—")


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

    # Состав: consist карточки, иначе «Состав: А — 2 шт» из описания,
    # иначе разбор НАЗВАНИЯ по кускам. Всё прогоняется через официальный
    # справочник Еды (eda_component) — недопустимые компоненты отклоняют импорт.
    raw_pos = []
    for c in item.get("consist") or []:
        try:
            raw_pos.append((clean_position_name(str(c.get("name", ""))), int(c.get("quantity", 1))))
        except (TypeError, ValueError):
            continue
    if not raw_pos:
        m_comp = re.search(r"Состав:\s*(.+?)(?:\.\s|\.$|$)", desc, re.S)
        if m_comp:
            for chunk in re.split(r",|\n", m_comp.group(1)):
                m_q = re.match(r"\s*(.+?)\s*[—-]\s*(\d+)\s*шт", chunk.strip())
                if m_q:
                    raw_pos.append((m_q.group(1), int(m_q.group(2))))
    if not raw_pos:
        chunks = [c.strip() for c in re.split(r"[,;/—–]|\s+-\s+|\sи\s", name) if c.strip()]
        for ch in chunks:
            m_q = re.search(r"\b(\d{1,3})\s*(?:шт|штук)", ch.lower())
            raw_pos.append((ch, int(m_q.group(1)) if m_q else 1))
        # «150 штук» в конце названия без привязки к куску — отдать единственному цветку
        m_g = re.search(r"\b(\d{1,3})\s*(?:шт|штук)\b", name.lower())
        if m_g:
            comps = to_eda_dpos(raw_pos)
            if len(comps) == 1:
                raw_pos = [(raw_pos[0][0], int(m_g.group(1)))] + raw_pos[1:]
    dpos = to_eda_dpos(raw_pos)
    if not dpos:
        return None, "не удалось определить состав (нет цветов из справочника Еды)"

    # Категория и название
    total_qty = sum(q for _, q in dpos)
    if len(dpos) == 1 and total_qty == 1 and not MONO_RX.search(name):
        # одна ветка — это «Цветы поштучно», название оставляем исходное
        cat = "Цветы поштучно"
        title = name[:100]
        width, height = 10, 45
    elif bool(MONO_RX.search(name)) or len(dpos) == 1:
        cat = "Монобукеты"
        # название — исходное с Flowwow (там есть сорт и количество);
        # генерация «Букет из N роз» теряла сорт и плодила «№2» (замечание владельца 24.11)
        title = name[:100]
        width, height = 25, 45
    else:
        cat = "Авторские букеты"
        title = name[:100]
        width, height = 35, 45

    consist_str = ",".join(f"{n[:1].upper() + n[1:]}:{q}" for n, q in dpos)

    # модерация Еды: чистим название от эмодзи, описание — от услуг/цен/доставки
    title = clean_eda_title(title)
    desc = clean_eda_description(desc, title)

    return [cat, CAT_IDS[cat], "", "", "", "",
            title, "",
            "1000", "г", "",
            str(eda_price),
            desc,
            "REN", "Россия",
            "", PLACES, "",
            ",".join(images),
            "", "", "",
            "", "1",
            str(width), str(height),
            consist_str], ""


def load_exclusions(path="eda_railway_ref.csv", extra_titles=()):
    """Что уже загружено на Еду: история импортов бота (ref CSV) + ранее принятые файлы.
    Возвращает (точные названия, пары (имя в «кавычках», размер))."""
    titles, pairs = set(extra_titles), set()
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            ref = list(csv.reader(f, delimiter=";"))
    except FileNotFoundError:
        return titles, pairs
    for r in ref[1:]:
        t = (r[6] if len(r) > 6 else "").strip()
        if not t:
            continue
        titles.add(t)
        m = re.search(r"«([^»]+)»\s*([A-Za-z])?", t)
        if m:
            pairs.add((m.group(1).strip().lower(), (m.group(2) or "").upper()))
    return titles, pairs


def row_pair(title):
    """('имя в кавычках', размер) из названия строки — для сверки с каталогом Еды."""
    m = re.search(r"«([^»]+)»", title)
    if not m:
        return None
    s = re.search(r"»\s*(?:размер\s+)?([SML])\b", title)
    return (m.group(1).strip().lower(), s.group(1) if s else "")


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

    # Исключаем то, что УЖЕ на Еде: история импортов бота + тестовая пятёрка
    # (eda_test5.csv пользователь уже загрузила — конфликт «уже существует в категории»)
    test5_titles = set()
    try:
        with open("eda_test5_titles.txt", encoding="utf-8") as f:
            test5_titles = {l.strip() for l in f if l.strip()}
    except FileNotFoundError:
        pass
    excl_titles, excl_pairs = set(), set()  # обновление по id: исключения не нужны, всё правим на месте
    kept = []
    for r in rows:
        t = r[6]
        pair = row_pair(t)
        if t in excl_titles or (pair and pair in excl_pairs):
            skipped.append(("", t[:60], "уже есть на Еде — исключён"))
            continue
        kept.append(r)
    rows = kept

    # Дедупликация названий: импортер отклоняет файл, если позиция с таким
    # названием уже встречалась (проверено 24.11 — «уже существует в строке N»)
    used = {}
    dups = 0
    for r in rows:
        title = r[6]
        if title in used:
            used[title] += 1
            dups += 1
            r[6] = f"{title[:92]} №{used[title]}"
        else:
            used[title] = 1
    if dups:
        print(f"переименовано дубликатов названий: {dups}")

    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=",")
        w.writerow(TEMPLATE_HEADERS)
        w.writerows(rows)

    mono = sum(1 for r in rows if r[0] == "Монобукеты")
    sht = sum(1 for r in rows if r[0] == "Цветы поштучно")
    print(f"OK: {out} — строк: {len(rows)} (моно {mono}, поштучно {sht}, "
          f"авторские {len(rows) - mono - sht}), пропущено: {len(skipped)}")
    for pid, nm, why in skipped[:20]:
        print(f"  пропуск {pid}: {nm} ({why})")


if __name__ == "__main__":
    main()
