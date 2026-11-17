"""Telegram-бот для взаимодействия с сервисом VIIO Consultant (Консультант) с поддержкой стриминга.

Запуск:
    python scripts/telegram_consultant_bot.py

Переменные окружения (или .env):
    TELEGRAM_BOT_TOKEN  - токен бота от @BotFather (обязательно)
    CONSULTANT_URL      - URL консультанта (по умолчанию http://localhost:8001/api/v1/consultant/chat)
    CONSULTANT_API_KEY  - ключ доступа (по умолчанию берётся из CONSULTANT_API_KEY в .env)
    CONSULTANT_TIMEOUT  - таймаут ответа в секундах (по умолчанию 90)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from typing import Any

from dotenv import load_dotenv
import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

# Загружаем переменные из .env в корне проекта
load_dotenv()

# Настройки
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CONSULTANT_URL = os.getenv("CONSULTANT_URL", "http://localhost:8001/api/v1/consultant/chat").strip()
CONSULTANT_STREAM_URL = CONSULTANT_URL.rstrip("/").replace("/chat", "/chat/stream")
CONSULTANT_API_KEY = os.getenv("CONSULTANT_API_KEY", "").strip()
CONSULTANT_TIMEOUT = float(os.getenv("CONSULTANT_TIMEOUT", "90.0"))

# Интервал обновления сообщения в Telegram при стриминге (лимит Telegram ~1 сек)
STREAM_EDIT_INTERVAL = 1.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("telegram_consultant_bot")

router = Router()


async def keep_typing(bot: Bot, chat_id: int, stop_event: asyncio.Event) -> None:
    """Периодически отправляет статус 'печатает...', пока формируется ответ LLM."""
    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception as e:
            logger.debug("Failed to send typing action: %s", e)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=4.5)
        except asyncio.TimeoutError:
            pass


async def query_consultant_sync(question: str) -> dict[str, Any]:
    """Фолбэк-запрос к обычному эндпоинту консультанта."""
    headers = {
        "Content-Type": "application/json",
        "X-Consultant-Key": CONSULTANT_API_KEY,
    }
    payload = {"message": question}

    async with httpx.AsyncClient(timeout=CONSULTANT_TIMEOUT) as client:
        response = await client.post(CONSULTANT_URL, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


def format_consultant_response(data: dict[str, Any]) -> str:
    """Форматирует ответ консультанта и список подтверждающих источников."""
    reply = data.get("reply", "").strip()
    sources = data.get("sources", [])

    if not reply:
        return "В опубликованных материалах нет сведений для ответа. Уточните вопрос."

    if not sources or "Источники:" in reply or "Источник:" in reply:
        return reply

    source_titles = []
    for s in sources:
        title = s.get("title") or s.get("publication_id") or "Документ"
        if title not in source_titles:
            source_titles.append(title)

    if source_titles:
        sources_text = "\n\n📚 **Источники:**\n" + "\n".join(f"• {t}" for t in source_titles)
        return reply + sources_text

    return reply


async def send_or_edit_long_message(
    bot: Bot, chat_id: int, target_message_id: int, text: str
) -> None:
    """Редактирует целевое сообщение, а если текст длиннее 4000 символов, досылает остаток новыми."""
    max_len = 4000
    if len(text) <= max_len:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=target_message_id, text=text)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise
        return

    # Если текст длинный: разбиваем по абзацам
    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = ""
    for para in paragraphs:
        if len(current_chunk) + len(para) + 2 > max_len:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
            if len(para) > max_len:
                for i in range(0, len(para), max_len):
                    chunks.append(para[i : i + max_len])
            else:
                current_chunk = para
        else:
            current_chunk = (current_chunk + "\n\n" + para).strip()
    if current_chunk:
        chunks.append(current_chunk)

    if chunks:
        await bot.edit_message_text(chat_id=chat_id, message_id=target_message_id, text=chunks[0])
        for extra in chunks[1:]:
            await bot.send_message(chat_id=chat_id, text=extra)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Обработчик команды /start."""
    welcome_text = (
        "Здравствуйте! 👋\n\n"
        "Я бот-консультант VIIO. Я помогаю находить ответы на вопросы по опубликованным "
        "регламентам, инструкциям, температурным режимам и нормативным материалам компании в режиме **онлайн-стриминга**.\n\n"
        "Просто напишите ваш вопрос обычным сообщением, например:\n"
        "• *Какой порядок приёмки термолабильных препаратов?*\n"
        "• *Какие категории заболеваний имеют право на бесплатный отпуск лекарств?*\n"
        "• *Где регистрируются отклонения температурного режима?*"
    )
    await message.answer(welcome_text, parse_mode="Markdown")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Обработчик команды /help."""
    help_text = (
        "ℹ️ **Как работать с консультантом:**\n\n"
        "1. Отправьте текстовый вопрос в чат.\n"
        "2. Бот выполняет гибридный поиск по опубликованным регламентам и онтологии и **стримит ответ токен за токеном**.\n"
        "3. В конце ответа всегда приводятся точные подтверждающие источники.\n"
        "4. Если информации в базе знаний нет, консультант прямо сообщит об этом.\n\n"
        "Команды:\n"
        "/start — Перезапуск диалога\n"
        "/help — Справка\n"
        "/status — Проверить доступность сервиса консультанта"
    )
    await message.answer(help_text, parse_mode="Markdown")


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    """Проверка доступности микросервиса Консультанта."""
    health_url = CONSULTANT_URL.replace("/api/v1/consultant/chat", "/health")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(health_url)
            if resp.status_code == 200:
                await message.answer(
                    f"✅ Сервис консультанта доступен: `{health_url}`\n"
                    f"Стриминг: `{CONSULTANT_STREAM_URL}`",
                    parse_mode="Markdown",
                )
            else:
                await message.answer(f"⚠️ Сервис ответил со статусом {resp.status_code}")
    except Exception as e:
        await message.answer(f"❌ Не удалось подключиться к консультанту: {e}")


@router.message(F.text)
async def handle_text_message(message: Message, bot: Bot) -> None:
    """Обработка текстовых вопросов пользователя со стримингом."""
    question = message.text.strip()
    if not question:
        return

    # Создаём начальное сервисное сообщение
    status_msg = await message.answer("🔍 *Ищу в опубликованных регламентах...*", parse_mode="Markdown")

    headers = {
        "Content-Type": "application/json",
        "X-Consultant-Key": CONSULTANT_API_KEY,
    }
    payload = {"message": question}

    accumulated_text = ""
    last_edit_time = time.monotonic()
    done_payload: dict[str, Any] | None = None
    stream_successful = False

    try:
        # Пробуем стриминговый запрос к консультанту
        async with httpx.AsyncClient(timeout=CONSULTANT_TIMEOUT) as client:
            async with client.stream("POST", CONSULTANT_STREAM_URL, headers=headers, json=payload) as resp:
                if resp.status_code == 200:
                    stream_successful = True
                    async for raw_line in resp.aiter_lines():
                        line = raw_line.strip()
                        if not line or not line.startswith("data: "):
                            continue

                        event_data = json.loads(line[6:])
                        ev_type = event_data.get("type")

                        if ev_type == "status":
                            status_text = event_data.get("message", "")
                            # Обновляем статус только если текст ответа ещё не начал генерироваться
                            if not accumulated_text and status_text:
                                now = time.monotonic()
                                if now - last_edit_time >= STREAM_EDIT_INTERVAL:
                                    try:
                                        await bot.edit_message_text(
                                            chat_id=message.chat.id,
                                            message_id=status_msg.message_id,
                                            text=f"⏳ *{status_text}*",
                                            parse_mode="Markdown",
                                        )
                                        last_edit_time = now
                                    except (TelegramBadRequest, TelegramRetryAfter):
                                        pass

                        elif ev_type == "delta":
                            delta = event_data.get("text", "")
                            accumulated_text += delta
                            now = time.monotonic()

                            # Троттлинг обновлений, чтобы не превысить лимиты Telegram Bot API
                            if now - last_edit_time >= STREAM_EDIT_INTERVAL and accumulated_text.strip():
                                preview = accumulated_text.strip() + " ▌"
                                if len(preview) < 4000:
                                    try:
                                        await bot.edit_message_text(
                                            chat_id=message.chat.id,
                                            message_id=status_msg.message_id,
                                            text=preview,
                                        )
                                        last_edit_time = now
                                    except TelegramRetryAfter as retry_err:
                                        await asyncio.sleep(retry_err.retry_after)
                                    except TelegramBadRequest as bad_req:
                                        if "message is not modified" not in str(bad_req):
                                            logger.debug("Telegram edit delta error: %s", bad_req)

                        elif ev_type == "done":
                            done_payload = event_data
                            break

                        elif ev_type == "error":
                            detail = event_data.get("detail", "Ошибка генерации")
                            await bot.edit_message_text(
                                chat_id=message.chat.id,
                                message_id=status_msg.message_id,
                                text=f"⚠️ {detail}",
                            )
                            return

        if stream_successful and done_payload is not None:
            formatted_final = format_consultant_response(done_payload)
            await send_or_edit_long_message(bot, message.chat.id, status_msg.message_id, formatted_final)
            return

    except (httpx.HTTPStatusError, httpx.RequestError, json.JSONDecodeError) as stream_err:
        logger.warning("Streaming failed or endpoint unavailable (%s), falling back to sync chat", stream_err)

    # Фолбэк на обычный non-streaming режим, если стриминг не удался
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(bot, message.chat.id, stop_typing))
    try:
        data = await query_consultant_sync(question)
        formatted_reply = format_consultant_response(data)
        await send_or_edit_long_message(bot, message.chat.id, status_msg.message_id, formatted_reply)
    except httpx.TimeoutException:
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            text="⏳ Время ожидания ответа истекло. Пожалуйста, повторите запрос позже.",
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_msg.message_id,
                text="🔒 Ошибка авторизации бота в сервисе Консультанта (проверьте CONSULTANT_API_KEY).",
            )
        elif e.response.status_code == 503:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_msg.message_id,
                text="⚠️ Сервис консультанта временно недоступен.",
            )
        else:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_msg.message_id,
                text=f"⚠️ Ошибка сервиса консультанта (код {e.response.status_code}).",
            )
    except Exception as e:
        logger.exception("Unexpected error in consultant bot: %s", e)
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            text="❌ Произошла ошибка при обращении к сервису. Попробуйте позже.",
        )
    finally:
        stop_typing.set()
        await typing_task


@router.message(~F.text)
async def handle_non_text_message(message: Message) -> None:
    """Предупреждение при отправке медиа/файлов/стикеров."""
    await message.answer("Пожалуйста, отправьте текстовый вопрос для консультанта.")


async def main() -> None:
    if not BOT_TOKEN:
        logger.error("Токен Telegram-бота не задан! Установите TELEGRAM_BOT_TOKEN в .env")
        sys.exit(1)

    logger.info("Инициализация Telegram-бота...")
    logger.info("Consultant Stream URL: %s", CONSULTANT_STREAM_URL)
    logger.info("Consultant Sync URL: %s", CONSULTANT_URL)

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот запущен и ожидает сообщений (streaming polling)...")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
