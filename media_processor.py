"""Обработка медиафайлов: сжатие и удаление звука из видео через ffmpeg."""

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Целевой потолок размера после сжатия (Маркет/Flowwow комфортно переваривают ~20 МБ)
TARGET_MAX_BYTES = 18 * 1024 * 1024


def _has_encoder() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def compress_video_sync(input_path: Path, output_path: Path) -> bool:
    """Сжатие видео: H.264, до 1080p по большей стороне, CRF 28, без звука.
    Если результат пустой/битый — фолбэк на исходник без перекодирования."""
    if not _has_encoder():
        logger.warning("ffmpeg не найден в системе (PATH). Видео будет загружено в оригинале.")
        return False
    attempts = [
        # 1) 1080p, CRF 28 — обычно ужимает 3-10x
        ["-vf", "scale='min(1080,iw)':-2", "-c:v", "libx264", "-preset", "fast",
         "-crf", "28", "-an", "-movflags", "+faststart"],
        # 2) 720p, CRF 30 — для совсем тяжёлых файлов
        ["-vf", "scale='min(720,iw)':-2", "-c:v", "libx264", "-preset", "fast",
         "-crf", "30", "-an", "-movflags", "+faststart"],
    ]
    for args in attempts:
        try:
            cmd = ["ffmpeg", "-y", "-i", str(input_path), *args, str(output_path)]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            if output_path.exists() and output_path.stat().st_size > 0:
                if output_path.stat().st_size >= input_path.stat().st_size:
                    # сжатие не дало выигрыша — небольшие файлы и так ок
                    continue
                return True
        except subprocess.CalledProcessError as e:
            logger.warning(f"ffmpeg попытка не удалась: {e.stderr.decode('utf-8', errors='ignore')[:200]}")
    # Фолбэк: просто снять звук без перекодирования
    try:
        cmd = ["ffmpeg", "-y", "-i", str(input_path), "-an", "-c:v", "copy", str(output_path)]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return output_path.exists() and output_path.stat().st_size > 0
    except subprocess.CalledProcessError as e:
        logger.error(f"Ошибка выполнения ffmpeg: {e.stderr.decode('utf-8', errors='ignore')[:300]}")
        return False


async def compress_video_async(input_path: Path, output_path: Path) -> bool:
    """Асинхронная обёртка сжатия видео."""
    return await asyncio.to_thread(compress_video_sync, input_path, output_path)


def mute_video_sync(input_path: Path, output_path: Path) -> bool:
    """Удаление аудиодорожки без перекодирования (оставлено для совместимости)."""
    try:
        cmd = ["ffmpeg", "-y", "-i", str(input_path), "-an", "-c:v", "copy", str(output_path)]
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
