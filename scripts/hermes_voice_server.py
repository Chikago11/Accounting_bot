"""Private HTTP entry point for the existing tool-free Hermes/STT worker.

Install next to accounting_voice_bridge.py inside Hermes. No Docker API, shell,
Telegram credentials or financial writes are exposed by this server.
"""

import asyncio
import fcntl
import hmac
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time

from aiohttp import web

WORKER = Path(__file__).with_name("accounting_voice_bridge.py")
MAX_BODY = 14_000_000
PID_FILE = Path("/opt/data/accounting-voice/server.pid")


def stop_server():
    try:
        pid = int(PID_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return

    def is_server():
        try:
            return str(Path(__file__).resolve()).encode() in Path(
                f"/proc/{pid}/cmdline"
            ).read_bytes().split(b"\0")
        except (FileNotFoundError, ProcessLookupError):
            return False

    if is_server():
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 210
        while is_server():
            if time.monotonic() >= deadline:
                raise TimeoutError("bridge_stop_timeout")
            time.sleep(0.1)


def validate_request(payload, model):
    if not isinstance(payload, dict):
        raise ValueError("invalid_request")
    timeout = payload.get("timeout")
    if type(timeout) is not int or not 5 <= timeout <= 300:
        raise ValueError("invalid_timeout")
    if payload.get("mode") == "transcribe":
        if set(payload) != {"mode", "audio", "timeout", "max_duration"}:
            raise ValueError("invalid_fields")
        if not isinstance(payload["audio"], str) or not 0 < len(payload["audio"]) <= 13_333_336:
            raise ValueError("invalid_audio")
        if type(payload["max_duration"]) is not int or not 1 <= payload["max_duration"] <= 120:
            raise ValueError("invalid_duration")
    elif payload.get("mode") == "parse":
        if set(payload) != {"mode", "instruction", "context", "timeout", "model"}:
            raise ValueError("invalid_fields")
        if payload["model"] != model:
            raise ValueError("invalid_model")
        if (
            not isinstance(payload["instruction"], str)
            or not 0 < len(payload["instruction"]) <= 16000
        ):
            raise ValueError("invalid_instruction")
        if not isinstance(payload["context"], dict) or len(json.dumps(payload["context"])) > 100000:
            raise ValueError("invalid_context")
    else:
        raise ValueError("invalid_mode")


async def run_worker(payload, worker=WORKER):
    # A process per request preserves the existing isolation and cleanup behavior.
    with tempfile.TemporaryDirectory(prefix="accounting-voice-") as temp:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(worker),
            "--worker",
            temp,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            output, _ = await asyncio.wait_for(
                process.communicate(json.dumps(payload).encode()), timeout=payload["timeout"]
            )
            if process.returncode or len(output) > 30000:
                raise ValueError("worker_failed")
            result = json.loads(output)
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise ValueError("worker_failed")
            return result
        finally:
            # Cancellation (including HTTP disconnect) must also stop STT children.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()


def create_app(key, model, runner=run_worker):
    if len(key) < 32 or not model:
        raise ValueError("Bridge key and model are required")
    lock = asyncio.Lock()

    @web.middleware
    async def authenticate(request, handler):
        if not hmac.compare_digest(
            request.headers.get("Authorization", "").encode(), f"Bearer {key}".encode()
        ):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        return await handler(request)

    async def health(request):
        return web.json_response({"ok": True, "model": model})

    async def handle(request):
        if lock.locked():
            return web.json_response({"ok": False, "error": "busy"}, status=429)
        async with lock:
            try:
                payload = await asyncio.wait_for(request.json(), timeout=10)
                validate_request(payload, model)
            except (ValueError, TypeError, asyncio.TimeoutError):
                return web.json_response({"ok": False, "error": "invalid_request"}, status=400)
            try:
                return web.json_response(await runner(payload))
            except Exception:
                # Never log provider errors, transcripts, request bodies or credentials.
                return web.json_response({"ok": False, "error": "bridge_failed"}, status=502)

    app = web.Application(middlewares=[authenticate], client_max_size=MAX_BODY)
    app.router.add_get("/health", health)
    app.router.add_post("/voice", handle)
    return app


if __name__ == "__main__":
    if sys.argv[1:] == ["--stop"]:
        stop_server()
        sys.exit(0)
    os.umask(0o077)
    key = (
        Path(os.getenv("VOICE_BRIDGE_KEY_FILE", "/opt/data/accounting-voice/bridge.key"))
        .read_text()
        .strip()
    )
    model = os.getenv("HERMES_MODEL", "gpt-5.6-luna")
    with PID_FILE.open("a+") as pid_file:
        fcntl.flock(pid_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pid_file.seek(0)
        pid_file.truncate()
        pid_file.write(str(os.getpid()))
        pid_file.flush()
        try:
            web.run_app(
                create_app(key, model),
                host="0.0.0.0",
                port=int(os.getenv("VOICE_BRIDGE_PORT", "8646")),
                access_log=None,
                handler_cancellation=True,
                print=lambda _: print("Accounting voice HTTP bridge started", flush=True),
            )
        finally:
            PID_FILE.unlink(missing_ok=True)
