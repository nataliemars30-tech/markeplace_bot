# REN Marketplace Bot

Telegram-бот автоматизации витрин цветочного магазина REN (Санкт-Петербург).
Сотрудник отправляет фото + цены + номер заказа МойСклада → бот считает
себестоимость, присваивает артикул, создаёт товар на Flowwow, складывает
фото на Яндекс Диск и пишет строку в Google-таблицу.

## Модули
- `bot.py` — мастер создания товара (FSM, белый список ID, приём медиагрупп)
- `flowwow_api.py` — создание товаров по Seller API Flowwow
- `moysklad_api.py` — себестоимость из заказа покупателя (закупочные цены позиций)
- `yandex_disk.py` — хранение фото/видео, публикация прямых ссылок
- `sheets_api.py` — листы категорий в Google-таблице (ключ из env или файла)
- `platform_rules.py` — категории Flowwow, коды ОКПД2, расчёт маржи
- `db.py` — SQLite: счётчики артикулов (REN-, REN-W-, REN-C-, REN-D-, REN-A-), история

## Запуск локально
1. `pip install -r requirements.txt`
2. Скопируй `.env.example` → `.env`, заполни токены
3. `python bot.py`

## Деплой на Railway
1. Приватный репозиторий на GitHub → Deploy from GitHub repo
2. Volume: Mount Path `/app/data` (SQLite не сбрасывается)
3. Variables: все из `.env.example`, Google-ключ — в `GOOGLE_CREDENTIALS_JSON` одной строкой
4. Dockerfile включает ffmpeg (удаление звука из видео)

## Заметки
- Бот отвечает только ID из `TELEGRAM_ALLOWED_IDS`
- Яндекс Еда создаётся вручную/через отдельный процесс (статус «в очереди» в таблице)
- Товар на Flowwow создаётся выключенным (isActive=false) — включение после проверки карточки
