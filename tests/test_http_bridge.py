import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestServer
import httpx

from scripts.hermes_voice_server import MAX_BODY, create_app, run_worker, stop_server
from voice.providers import HermesBridge, ProviderError
from voice.settings import VoiceSettings

KEY = "synthetic-test-key-" * 3
MODEL = "gpt-5.6-luna"
PAYLOAD = {"mode": "parse", "instruction": "parse", "context": {}, "timeout": 5, "model": MODEL}
RESULT = {"ok": True, "result": "synthetic result", "model": MODEL}


class HttpSettingsTests(unittest.TestCase):
    def test_stale_pid_file_cannot_stop_an_unrelated_process(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "server.pid"
            pid_file.write_text(str(os.getpid()))
            with patch("scripts.hermes_voice_server.PID_FILE", pid_file), patch("os.kill") as kill:
                stop_server()
                kill.assert_not_called()

    def test_http_transport_and_ambiguous_configuration(self):
        env = {
            "VOICE_INPUT_ENABLED": "true",
            "VOICE_BRIDGE_URL": "http://hermes:8646/voice",
            "VOICE_BRIDGE_KEY_FILE": "/run/secrets/bridge-key",
            "VOICE_BRIDGE_COMMAND": "",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = VoiceSettings.from_env()
            self.assertEqual(settings.bridge_url, env["VOICE_BRIDGE_URL"])
            self.assertFalse(settings.bridge_command)
            os.environ["VOICE_BRIDGE_COMMAND"] = '["docker"]'
            with self.assertRaises(ValueError):
                VoiceSettings.from_env()

    def test_endpoint_cannot_embed_credentials_or_use_other_protocols(self):
        env = {"VOICE_INPUT_ENABLED": "true", "VOICE_BRIDGE_KEY_FILE": "/key"}
        for url in (
            "file:///key",
            "http://user:password@host/voice",
            "http:///voice",
            "http://host/voice?key=x",
        ):
            with (
                self.subTest(url=url),
                patch.dict(os.environ, env | {"VOICE_BRIDGE_URL": url}, clear=True),
            ):
                with self.assertRaises(ValueError):
                    VoiceSettings.from_env()


class HttpBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.key_file = Path(self.temp.name) / "key"
        self.key_file.write_text(KEY)
        self.runner = AsyncMock(return_value=RESULT)
        self.server = TestServer(create_app(KEY, MODEL, self.runner))
        await self.server.start_server()
        self.addAsyncCleanup(self.server.close)
        self.url = str(self.server.make_url("/voice"))
        self.bridge = HermesBridge(
            replace(VoiceSettings(), bridge_url=self.url, bridge_key_file=self.key_file, timeout=5)
        )

    async def post(self, payload, key=KEY):
        async with httpx.AsyncClient(trust_env=False) as client:
            return await client.post(
                self.url, headers={"Authorization": f"Bearer {key}"}, json=payload
            )

    async def test_authenticated_client_passes_json_to_fixed_worker(self):
        self.assertEqual(await self.bridge.call(PAYLOAD), RESULT)
        self.runner.assert_awaited_once_with(PAYLOAD)
        self.runner.reset_mock()
        self.assertEqual((await self.post(PAYLOAD, key="wrong")).status_code, 401)
        self.runner.assert_not_awaited()

    async def test_both_modes_and_rejection_of_arbitrary_commands(self):
        audio = {"mode": "transcribe", "audio": "YQ==", "max_duration": 1, "timeout": 5}
        self.assertEqual((await self.post(audio)).status_code, 200)
        self.runner.reset_mock()
        for payload in (
            PAYLOAD | {"command": "do not execute"},
            PAYLOAD | {"model": "gpt-5.6-sol"},
            PAYLOAD | {"timeout": 301},
            PAYLOAD | {"timeout": True},
            PAYLOAD | {"mode": "shell"},
            audio | {"path": "/etc/passwd"},
            audio | {"max_duration": 121},
        ):
            with self.subTest(payload=payload):
                self.assertEqual((await self.post(payload)).status_code, 400)
        self.runner.assert_not_awaited()

    async def test_oversized_request_is_rejected_before_worker(self):
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.post(
                self.url,
                headers={"Authorization": f"Bearer {KEY}"},
                content=b"x" * (MAX_BODY + 1),
            )
        self.assertEqual(response.status_code, 413)
        self.runner.assert_not_awaited()

    async def test_worker_errors_are_not_exposed(self):
        self.runner.side_effect = RuntimeError("fake confidential provider detail")
        response = await self.post(PAYLOAD)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {"ok": False, "error": "bridge_failed"})

    async def test_client_rejects_large_and_invalid_responses(self):
        for result in ({"ok": False}, [], RESULT | {"result": "x" * 30001}):
            self.runner.return_value = result
            with self.subTest(result_type=type(result).__name__), self.assertRaises(ProviderError):
                await self.bridge.call(PAYLOAD)

    async def test_second_request_is_rejected_while_worker_is_busy(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow(_):
            entered.set()
            await release.wait()
            return RESULT

        self.runner.side_effect = slow
        first = asyncio.create_task(self.bridge.call(PAYLOAD))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            self.assertEqual((await self.post(PAYLOAD)).status_code, 429)
        finally:
            release.set()
            await first

    async def test_client_cancellation_stops_server_work(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def slow(_):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.runner.side_effect = slow
        task = asyncio.create_task(self.bridge.call(PAYLOAD))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(cancelled.wait(), 2)


class WorkerCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_kills_worker_and_child_and_removes_audio_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            info = Path(directory) / "processes.json"
            worker = Path(directory) / "worker.py"
            worker.write_text(
                "import json,os,pathlib,subprocess,sys,time\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                f"pathlib.Path({str(info)!r}).write_text(json.dumps({{'pid':os.getpid(), 'child':child.pid, 'temp':sys.argv[2]}}))\n"
                "time.sleep(30)\n"
            )
            task = asyncio.create_task(run_worker(PAYLOAD, worker))
            try:
                for _ in range(200):
                    if info.exists():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(info.exists())
                processes = json.loads(info.read_text())
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertFalse(Path(processes["temp"]).exists())
            for pid in (processes["pid"], processes["child"]):
                path = Path(f"/proc/{pid}/status")
                if path.exists():
                    self.assertIn("State:\tZ", path.read_text())
