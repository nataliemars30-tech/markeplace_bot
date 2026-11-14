"""Слой локальной базы данных SQLite для генерации артикулов и хранения истории."""

import sqlite3
from typing import Optional, List, Dict, Any
from config import config


def get_connection() -> sqlite3.Connection:
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Инициализация схемы таблиц базы данных."""
    with get_connection() as conn:
        cursor = conn.cursor()

        # Таблица счетчиков артикулов по префиксам
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS counters (
                prefix TEXT PRIMARY KEY,
                current_value INTEGER NOT NULL
            );
        """)

        # Стартовые значения счетчиков
        cursor.execute("INSERT OR IGNORE INTO counters (prefix, current_value) VALUES ('REN', 0);")
        cursor.execute("INSERT OR IGNORE INTO counters (prefix, current_value) VALUES ('REN-W', 0);") # Свадебные
        cursor.execute("INSERT OR IGNORE INTO counters (prefix, current_value) VALUES ('REN-C', 0);") # Композиции
        cursor.execute("INSERT OR IGNORE INTO counters (prefix, current_value) VALUES ('REN-D', 0);") # Декор
        cursor.execute("INSERT OR IGNORE INTO counters (prefix, current_value) VALUES ('REN-A', 0);") # Доп. товары

        # Таблица продуктов и вариаций
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                base_article TEXT NOT NULL,
                full_article TEXT NOT NULL UNIQUE,
                root_category TEXT NOT NULL,
                name TEXT NOT NULL,
                variation TEXT,
                price REAL NOT NULL,
                cost_price REAL NOT NULL,
                margin_percent REAL NOT NULL,
                moysklad_order_no TEXT,
                flowwow_id TEXT,
                tilda_category TEXT,
                okpd2_code TEXT,
                yandex_status TEXT DEFAULT 'в очереди',
                cover_url TEXT,
                all_photos_json TEXT,
                video_url TEXT,
                created_by_tg_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()


# ------------------ Отзывы Flowwow ------------------

def save_review(review: Dict[str, Any], draft: str = "") -> bool:
    """Сохранить отзыв, если его ещё нет. Возвращает True для новых."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                review_id INTEGER PRIMARY KEY,
                shop_score INTEGER,
                author TEXT,
                text TEXT,
                review_date TEXT,
                answer_shop TEXT,
                draft TEXT,
                notified_at TIMESTAMP,
                answered_at TIMESTAMP,
                answer_text TEXT
            );
        """)
        try:
            cursor.execute("SELECT draft FROM reviews LIMIT 1;")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE reviews ADD COLUMN draft TEXT;")
        cursor.execute(
            "INSERT OR IGNORE INTO reviews (review_id, shop_score, author, text, review_date, answer_shop, draft, notified_at) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP);",
            (review.get("id"), review.get("shopScore"), review.get("authorName", ""),
             review.get("text", ""), review.get("date", ""), review.get("answerShop", ""), draft)
        )
        conn.commit()
        return cursor.rowcount > 0


def get_review_draft(review_id: int) -> str:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT draft FROM reviews WHERE review_id = ?;", (review_id,)
        ).fetchone()
        return row["draft"] if row and row["draft"] else ""


def mark_review_answered(review_id: int, answer: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE reviews SET answered_at = CURRENT_TIMESTAMP, answer_text = ? WHERE review_id = ?;",
            (answer, review_id)
        )
        conn.commit()


def get_pending_reviews() -> List[Dict[str, Any]]:
    """Отзывы без ответа магазина (для команды /otvet без аргументов)."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT review_id, shop_score, author, text, review_date
            FROM reviews WHERE answered_at IS NULL ORDER BY review_id DESC LIMIT 10;
        """)
        return [dict(row) for row in cursor.fetchall()]


def get_product_by_article(full_article: str) -> Optional[Dict[str, Any]]:
    """Локальная запись товара по полному артикулу (для /sync)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT base_article, full_article, name, variation, price, tilda_category, cover_url "
            "FROM products WHERE full_article = ? ORDER BY id DESC LIMIT 1;",
            (full_article,)
        ).fetchone()
        return dict(row) if row else None


def get_next_article(root_category: str) -> str:
    """Генерация следующего сквозного артикула с префиксом в зависимости от категории."""
    prefix_map = {
        "Букеты": "REN",
        "Свадебные": "REN-W",
        "Композиции": "REN-C",
        "Декор": "REN-D",
        "Доп. товары": "REN-A"
    }
    prefix = prefix_map.get(root_category, "REN")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE;")
        cursor.execute("SELECT current_value FROM counters WHERE prefix = ?;", (prefix,))
        row = cursor.fetchone()
        next_val = (row["current_value"] if row else 0) + 1
        cursor.execute("UPDATE counters SET current_value = ? WHERE prefix = ?;", (next_val, prefix))
        conn.commit()
        return f"{prefix}-{next_val:03d}"


def save_product_variation(
    base_article: str,
    full_article: str,
    root_category: str,
    name: str,
    variation: str,
    price: float,
    cost_price: float,
    margin_percent: float,
    moysklad_order_no: str,
    flowwow_id: str,
    tilda_category: str,
    okpd2_code: str,
    cover_url: str,
    all_photos_json: str,
    video_url: Optional[str],
    created_by_tg_id: int
) -> None:
    """Сохранение карточки товара/вариации в локальную историю."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO products (
                base_article, full_article, root_category, name, variation,
                price, cost_price, margin_percent, moysklad_order_no,
                flowwow_id, tilda_category, okpd2_code, cover_url,
                all_photos_json, video_url, created_by_tg_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            base_article, full_article, root_category, name, variation,
            price, cost_price, margin_percent, moysklad_order_no,
            flowwow_id, tilda_category, okpd2_code, cover_url,
            all_photos_json, video_url, created_by_tg_id
        ))
        conn.commit()


def get_all_products_for_feed() -> List[Dict[str, Any]]:
    """Все вариации товаров для YML-фида Яндекс Карт."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT base_article, full_article, root_category, name, variation,
                   price, cover_url, created_at
            FROM products
            ORDER BY id;
        """)
        return [dict(row) for row in cursor.fetchall()]
