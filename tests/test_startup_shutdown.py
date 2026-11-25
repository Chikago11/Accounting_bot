import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

import main

ROOT = Path(__file__).resolve().parent.parent


class StartupTests(unittest.TestCase):
    def test_invalid_timezone_fails_at_config_load_with_voice_on_or_off(self):
        for voice_enabled in ("false", "true"):
            with self.subTest(voice=voice_enabled):
                env = dict(
                    os.environ,
                    APP_TIMEZONE="Invalid/AccountingZone",
                    VOICE_INPUT_ENABLED=voice_enabled,
                )
                result = subprocess.run(
                    [sys.executable, "-c", "import config"],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Invalid APP_TIMEZONE/VOICE_TIMEZONE", result.stderr)

    def test_valid_timezone_starts_with_voice_off(self):
        env = dict(os.environ, APP_TIMEZONE="Asia/Bangkok", VOICE_INPUT_ENABLED="false")
        result = subprocess.run(
            [sys.executable, "-c", "import config"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_legacy_timezone_is_also_validated(self):
        env = dict(
            os.environ,
            APP_TIMEZONE="",
            VOICE_TIMEZONE="Invalid/AccountingZone",
            VOICE_INPUT_ENABLED="false",
        )
        result = subprocess.run(
            [sys.executable, "-c", "import config"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid APP_TIMEZONE/VOICE_TIMEZONE", result.stderr)


class ShutdownTests(unittest.TestCase):
    def test_sigint_cleans_up_without_traceback(self):
        # Run the real lifecycle with an offline application; no Telegram polling.
        code = """
import main
class Updater:
    async def start_polling(self): pass
    async def stop(self): print('UPDATER_STOPPED')
class Application:
    updater = Updater()
    async def initialize(self): pass
    async def start(self): pass
    async def stop(self): print('APP_STOPPED')
    async def shutdown(self): print('SHUTDOWN_DONE')
main.build_application = Application
main.run()
"""
        with subprocess.Popen(
            [sys.executable, "-u", "-c", code],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as process:
            try:
                ready, _, _ = select.select([process.stdout], [], [], 10)
                self.assertTrue(ready, "Offline bot failed to start")
                self.assertEqual(process.stdout.readline().strip(), "Bot started...")
                process.send_signal(signal.SIGINT)
                output, error = process.communicate(timeout=10)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        self.assertEqual(process.returncode, 0, error)
        self.assertEqual(output.splitlines(), ["UPDATER_STOPPED", "APP_STOPPED", "SHUTDOWN_DONE"])
        self.assertNotIn("KeyboardInterrupt", error)
        self.assertNotIn("Traceback", error)

    def test_other_exceptions_are_not_suppressed(self):
        with patch.object(main, "main", AsyncMock(side_effect=RuntimeError("synthetic failure"))):
            with self.assertRaises(RuntimeError):
                main.run()
