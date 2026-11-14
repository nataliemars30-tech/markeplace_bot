"""FSM-состояния мастера добавления товаров (меню v2)."""

from aiogram.fsm.state import State, StatesGroup


class ProductCreationFSM(StatesGroup):
    product_type = State()       # "author", "mono", "box", "wedding", "decor", "extra"
    sizes_mode = State()         # "single" (1 размер) или "multi" (несколько размеров)
    flower_type = State()        # Для монобукетов: выбранный цветок
    color = State()              # Выбранный цвет из 8 вариантов
    tilda_categories = State()   # Выбранные разделы Tilda (список строк)
    waiting_for_upload = State() # Ожидание медиа и строки параметров в подписи


class HideFSM(StatesGroup):
    waiting_article = State()    # Ожидание артикула для скрытия со всех витрин


class SyncFSM(StatesGroup):
    choosing = State()           # Выбор площадок для копирования (кнопки)
