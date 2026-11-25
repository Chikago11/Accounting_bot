import asyncio
import fcntl
import json
import logging
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from catalog import ACTION_LABELS, CATEGORY_LABELS

from .models import parse_result
from .service import VoiceService

log = logging.getLogger(__name__)


class VoiceController:
    def __init__(self, settings, store, stt, parser, execute, allowed_users, menu_factory):
        self.settings, self.store = settings, store
        self.stt, self.parser = stt, parser
        self.allowed_users, self.menu_factory = allowed_users, menu_factory
        self.service = VoiceService(store, settings, execute)
        self.busy = asyncio.Lock()
        self.worker_lock = None

    def acquire_worker(self):
        self.worker_lock = (self.settings.data_dir / "voice-worker.lock").open("a")
        fcntl.flock(self.worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.store.recover()
        self.store.purge_transcripts()

    def allowed(self, update):
        return bool(update.effective_user and update.effective_user.id in self.allowed_users)

    async def receive(self, update, context):
        if not self.allowed(update):
            return
        message = update.effective_message
        voice = message.voice
        duration = (
            voice.duration.total_seconds()
            if hasattr(voice.duration, "total_seconds")
            else voice.duration
        )
        if (
            not (0 < duration <= self.settings.max_duration)
            or not voice.file_size
            or voice.file_size > self.settings.max_bytes
        ):
            await message.reply_text(
                f"Отправьте голосовое до {self.settings.max_duration} секунд и {self.settings.max_bytes // 1000000} МБ."
            )
            return
        if self.busy.locked():
            await message.reply_text("Ещё обрабатываю голосовое. Попробуйте через минуту.")
            return
        async with self.busy:
            self.store.purge_transcripts()
            source = f"{context.bot.id}:{message.chat_id}:{message.message_id}:{update.effective_user.id}"
            batch, fresh = self.store.reserve(source, update.effective_user.id, message.chat_id)
            if not fresh:
                await message.reply_text(
                    "Это голосовое уже принято. Используйте подтверждение под предыдущим результатом или отправьте новое сообщение."
                )
                return
            key = batch["id"]
            context.user_data.update(mode=None, category=None)
            stage = "download"
            started = time.monotonic()
            try:
                await message.reply_text("Распознаю голосовое…")
                file = await context.bot.get_file(voice.file_id, read_timeout=30)
                if file.file_size and file.file_size > self.settings.max_bytes:
                    raise ValueError("file_too_large")
                audio = await file.download_as_bytearray(read_timeout=30)
                if not 0 < len(audio) <= self.settings.max_bytes:
                    raise ValueError("file_too_large")
                stage = "stt"
                transcript = await self.stt.transcribe(bytes(audio))
                del audio
                if (
                    not isinstance(transcript, str)
                    or not transcript.strip()
                    or len(transcript) > 6000
                ):
                    raise ValueError("invalid_transcript")
                log.info("voice stage=stt batch=%s elapsed=%.2f", key, time.monotonic() - started)
                stage = "parse"
                parsing_started = time.monotonic()
                result = await self.parser.parse(transcript)
                operations, clarification = parse_result(result, self.settings)
                log.info(
                    "voice stage=parse batch=%s elapsed=%.2f count=%s",
                    key,
                    time.monotonic() - parsing_started,
                    len(operations),
                )
                if clarification:
                    self.store.finish(key, "cancelled")
                    await message.reply_text(
                        f"Нужно уточнение: {clarification}\nПовторите голосовое с уточнением или воспользуйтесь кнопками.",
                        reply_markup=self.menu_factory(),
                    )
                    return
                self.store.pending(key, transcript, operations)
                # Keep each Telegram message within its 4096-character limit.
                for start in range(0, len(transcript), 3500):
                    await message.reply_text("Распознано:\n" + transcript[start : start + 3500])
                text = "Проверьте операции:\n\n" + "\n".join(
                    f"{i}. {op.describe()}" for i, op in enumerate(operations, 1)
                )
                text += f"\n\nПодтверждение действует {self.settings.ttl // 60} мин."
                await message.reply_text(
                    text,
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "✅ Подтвердить", callback_data=f"voice:confirm:{key}"
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    "✏️ Изменить", callback_data=f"voice:edit:{key}"
                                ),
                                InlineKeyboardButton(
                                    "❌ Отмена", callback_data=f"voice:cancel:{key}"
                                ),
                            ],
                        ]
                    ),
                )
                log.info(
                    "voice stage=pending batch=%s user=%s chat=%s message=%s",
                    key,
                    update.effective_user.id,
                    message.chat_id,
                    message.message_id,
                )
            except Exception as error:
                self.store.finish(key, "failed")
                log.warning("voice stage=%s batch=%s error=%s", stage, key, type(error).__name__)
                errors = {
                    "download": "Не удалось скачать голосовое. Попробуйте отправить его ещё раз.",
                    "stt": "Не удалось распознать речь. Попробуйте короткое голосовое с чёткой суммой.",
                    "parse": "Не удалось однозначно разобрать операции. Повторите сообщение с категорией и суммой или используйте кнопки.",
                }
                await message.reply_text(errors[stage], reply_markup=self.menu_factory())

    def result_text(self, batch):
        if batch is None:
            return "Пакет не найден или принадлежит другому пользователю."
        status = batch["status"]
        messages = {
            "processing": "Операции уже выполняются. Повторно нажмите позже, чтобы увидеть результат.",
            "cancelled": "Операции отменены.",
            "expired": "Подтверждение устарело. Отправьте новое голосовое.",
            "pending": "Операции ожидают подтверждения.",
            "analyzing": "Голосовое ещё обрабатывается.",
        }
        if status in messages:
            return messages[status]
        attempts = self.store.attempts(batch["id"])
        items = json.loads(batch["operations"])
        lines = ["Результат записи:"]
        for position, item in enumerate(items):
            state = attempts.get(position)
            label = (
                "✅ Записано"
                if state == "completed"
                else "⚠️ Результат неизвестен"
                if state in ("uncertain", "executing")
                else "⏸ Не записано"
            )
            category = CATEGORY_LABELS.get(item["category_id"], "")
            lines.append(
                f"{position + 1}. {label}: {ACTION_LABELS[item['action']]} {category} — {item['amount']} {item['currency']}"
            )
        if status == "failed":
            if "rejected" in attempts.values():
                lines.append(
                    "Проверьте таблицу: для каждой категории нужна одна строка перед «Итого». "
                    "У нового месяца должны быть свободные ячейки. "
                    "После исправления внесите только незаписанные операции заново."
                )
            lines.append(
                "Запись остановлена. Проверьте отмеченные операции в таблице перед повторным вводом; автоматического повтора не будет."
            )
        return "\n".join(lines)

    async def callback(self, update, context):
        query = update.callback_query
        if not self.allowed(update):
            await query.answer("Нет доступа", show_alert=True)
            return
        parts = (query.data or "").split(":")
        if len(parts) != 3 or parts[1] not in ("confirm", "cancel", "edit"):
            await query.answer("Некорректная кнопка")
            return
        action, key = parts[1:]
        user_id, chat_id = update.effective_user.id, update.effective_chat.id
        batch = self.store.get(key, user_id, chat_id)
        if batch is None:
            await query.answer("Этот пакет недоступен", show_alert=True)
            return
        await query.answer()
        if action == "confirm":
            batch = await asyncio.to_thread(self.service.confirm, key, user_id, chat_id)
        else:
            changed = self.store.cancel(key, user_id, chat_id)
            if changed and action == "edit":
                context.user_data.update(mode=None, category=None)
                await query.edit_message_text(
                    "Пакет отменён. Внесите исправленные операции через меню или отправьте новое голосовое.",
                    reply_markup=self.menu_factory(),
                )
                return
            batch = self.store.get(key, user_id, chat_id)
        text = self.result_text(batch)
        # A callback whose delivery was retried can already show this exact result.
        if update.effective_message.text != text:
            markup = (
                InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "Проверить результат", callback_data=f"voice:confirm:{key}"
                            )
                        ]
                    ]
                )
                if batch and batch["status"] == "processing"
                else None
            )
            await query.edit_message_text(text, reply_markup=markup)
