"""Opt-in real STT/Hermes check with fake Telegram and fake financial writes.

Use synthetic audio saying: «Добавь расход на магазин тысячу бат и на бензин
пятьсот бат». This script cannot connect to Google Sheets or send Telegram messages.
"""

import argparse
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice.handler import VoiceController
from voice.models import parse_result
from voice.providers import HermesBridge
from voice.settings import VoiceSettings
from voice.store import PendingStore


async def smoke(args):
    with tempfile.TemporaryDirectory(prefix="accounting-smoke-") as temp:
        settings = replace(
            VoiceSettings(),
            enabled=True,
            data_dir=Path(temp),
            bridge_command=tuple(json.loads(args.command)),
        )
        bridge = HermesBridge(settings)
        written = []
        store = PendingStore(settings.data_dir / "voice.sqlite3")
        if args.audio:
            audio = args.audio.read_bytes()
            message = NS(
                chat_id=10,
                message_id=1,
                text="preview",
                reply_text=AsyncMock(),
                voice=NS(duration=1, file_size=len(audio), file_id="synthetic"),
            )
            update = NS(
                effective_user=NS(id=42), effective_chat=NS(id=10), effective_message=message
            )
            file = NS(
                file_size=len(audio), download_as_bytearray=AsyncMock(return_value=bytearray(audio))
            )
            context = NS(bot=NS(id=123, get_file=AsyncMock(return_value=file)), user_data={})
            controller = VoiceController(
                settings, store, bridge, bridge, written.append, {42}, lambda: None
            )
            await controller.receive(update, context)
            with store.connect() as db:
                row = dict(db.execute("SELECT * FROM batches").fetchone())
            assert row["status"] == "pending", "Audio did not produce a pending package"
            assert not written, "Write before confirmation"
            update.callback_query = NS(
                data=f"voice:confirm:{row['id']}", answer=AsyncMock(), edit_message_text=AsyncMock()
            )
            await controller.callback(update, context)
            await controller.callback(update, context)
            assert len(written) == 2, (
                "Confirmation must write exactly twice, even with duplicate click"
            )
            operations = written
            print("Real STT + Hermes + preview + confirmation + duplicate protection: OK")
        else:
            result = await bridge.parse(
                "Добавь расход на магазин тысячу бат и на бензин пятьсот бат"
            )
            operations, clarification = parse_result(result, settings)
            assert clarification is None
            print("Real Hermes parser: OK")
        assert [(o.action, o.category_id, o.amount) for o in operations] == [
            ("expense_add", "supermarket", 1000),
            ("expense_add", "fuel", 500),
        ]
        print("Categories and amounts: OK; production writes: 0; Telegram messages: 0")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audio", type=Path, help="Synthetic OGG/Opus audio for the example phrase"
    )
    parser.add_argument(
        "--command", default=os.getenv("VOICE_BRIDGE_COMMAND"), help="JSON command argument array"
    )
    args = parser.parse_args()
    if not args.command:
        parser.error("Set VOICE_BRIDGE_COMMAND or --command")
    asyncio.run(smoke(args))
