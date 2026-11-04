# Деплой бота REN на Railway (исправленная версия)

## Структура
- `Dockerfile` — образ Python 3.12, без лишних пакетов
- `app/bot.py` — код бота (контракт: env-переменные, SQLite в /app/data, health-сервер)
- `app/health.py` — отвечает `ok` на `/` (защита от «засыпания» Railway)
- `data/` — сюда монтируется Volume (SQLite)

## Шаги

1. **GitHub**: создать приватный репозиторий `flowwow-bot`.
2. **Локально** (в папке проекта):
   ```cmd
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/ВАШ_АККАУНТ/flowwow-bot.git
   git push -u origin main
   ```
3. **Railway**: New Project → Deploy from GitHub repo → выбрать `flowwow-bot` → Deploy.
   Тип сервиса: Web Service (health-сервер на порту 8080 держит его живым).
4. **Volume**: Settings → Volumes → Add Volume → Mount Path: `/app/data`.
5. **Variables** (вкладка Variables):
   | Переменная | Значение |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | токен от @BotFather |
   | `TELEGRAM_ALLOWED_IDS` | Telegram ID через запятую |
   | `FLOWWOW_TOKEN` | токен Flowwow API |
   | `FLOWWOW_SHOP_ID` | `222976` |
   | `MOYSKLAD_TOKEN` | токен МойСклад |
   | `YANDEX_MARKET_API_KEY` | ключ Маркета (ACMA:...) |
   | `GOOGLE_CREDENTIALS_JSON` | весь JSON сервисного аккаунта одной строкой |
   | `GOOGLE_SPREADSHEET_KEY` | ID Google-таблицы |
   | `YANDEX_DISK_TOKEN` | токен Яндекс Диск (для фото) |

   ВАЖНО: `google_credentials.json` в Git не коммитить — только переменная окружения.
6. **Проверка**: View Logs → ожидаем «Запуск бота...» → в Telegram `/start`.

## Деньги
Бесплатного тарифа у Railway нет: пробный кредит ~$5, далее от ~$5/мес.
Альтернатива дешевле: VPS (~250₽/мес), тот же Docker-образ, деплой любым удобным способом.
