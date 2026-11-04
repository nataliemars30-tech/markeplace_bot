"""Обработка медиафайлов: сохранение очередности и удаление звука из видео через ffmpeg."""

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def mute_video_sync(input_path: Path, output_path: Path) -> bool:
    """Удаление аудиодорожки из видеофайла через ffmpeg без перекодирования видео."""
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(input_path),
            "-an",
            "-c:v", "copy",
            str(output_path)
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except FileNotFoundError:
        logger.warning("ffmpeg не найден в системе (PATH). Видео будет загружено в оригинале.")
        return False
    except subprocess.CalledProcessError as e:
        logger.error(f"Ошибка выполнения ffmpeg: {e.stderr.decode('utf-8', errors='ignore')}")
        return False


async def mute_video_async(input_path: Path, output_path: Path) -> bool:
    """Асинхронная обертка для удаления звука."""
    return await asyncio.to_thread(mute_video_sync, input_path, output_path)
