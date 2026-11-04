# -*- coding: utf-8 -*-
"""Healthcheck-сервер: отдаёт 200 OK на /, чтобы Railway не считал бота зависшим.
Запускается как фоновая задача из bot.py."""
import os
from aiohttp import web


async def handle(request):
    return web.Response(text="ok")


def start_health_server():
    app = web.Application()
    app.router.add_get("/", handle)
    port = int(os.environ.get("PORT", "8080"))
    runner = web.AppRunner(app)
    # синхронный запуск в отдельном потоке aiohttp совместим с asyncio:
    # вызывать из async-контекста через await runner.setup()
    return runner, port
