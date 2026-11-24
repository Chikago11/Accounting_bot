import asyncio
from dataclasses import replace
import os
from pathlib import Path
import sys
import tempfile
import unittest

from voice.providers import HermesBridge, ProviderError
from voice.settings import VoiceSettings


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_payload_is_stdin_data_and_not_shell_code(self):
        code = "import json,sys; x=json.load(sys.stdin); print(json.dumps({'ok':True,'result':x['payload']}))"
        settings = replace(VoiceSettings(), bridge_command=(sys.executable, "-c", code))
        payload = 'quotes " `cmd` $(do_not_execute)\nnew line'
        result = await HermesBridge(settings).call({"payload": payload})
        self.assertEqual(result["result"], payload)

    async def test_protocol_and_exit_fail_closed(self):
        for code in ("print('not json')", "print('{\"ok\":false}')", "raise SystemExit(2)"):
            settings = replace(VoiceSettings(), bridge_command=(sys.executable, "-c", code))
            with self.subTest(code=code), self.assertRaises(ProviderError):
                await HermesBridge(settings).call({})

    async def test_cancellation_kills_local_subprocess(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "pid"
            code = "import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
            settings = replace(
                VoiceSettings(), bridge_command=(sys.executable, "-c", code, str(pid_file))
            )
            task = asyncio.create_task(HermesBridge(settings).call({}))
            try:
                for _ in range(100):
                    if pid_file.exists():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(pid_file.exists())
                pid = int(pid_file.read_text())
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
