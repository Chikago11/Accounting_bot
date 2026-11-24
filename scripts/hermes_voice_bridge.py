"""Install inside the Hermes container; JSON stdin/stdout, no bot credentials.

The outer supervisor enforces a deadline inside Docker (killing docker exec on
the host alone does not kill the container process). No gateway is started.
"""

import base64
import contextlib
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile


def transcribe(request, temp):
    from tools.transcription_tools import transcribe_audio

    audio = base64.b64decode(request["audio"], validate=True)
    if not 0 < len(audio) <= 10_000_000:
        raise ValueError("audio_size")
    path = temp / "voice.ogg"
    path.write_bytes(audio)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True,
        check=True,
        timeout=10,
    )
    duration = float(json.loads(probe.stdout)["format"]["duration"])
    if not 0 < duration <= min(120, request["max_duration"]):
        raise ValueError("audio_duration")
    result = transcribe_audio(str(path))
    text = result.get("transcript")
    if not result.get("success") or not isinstance(text, str) or not text.strip():
        raise ValueError("transcription_failed")
    return {"ok": True, "transcript": text.strip()}


def parse(request):
    from gateway.run import _resolve_gateway_model, _resolve_runtime_agent_kwargs
    from run_agent import AIAgent

    runtime = _resolve_runtime_agent_kwargs()
    model = runtime.pop("model", None) or _resolve_gateway_model()
    agent = AIAgent(
        model=model,
        **runtime,
        enabled_toolsets=[],
        max_iterations=1,
        save_trajectories=False,
        verbose_logging=False,
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
        skip_background_review=True,
        session_db=None,
        ephemeral_system_prompt=request["instruction"],
        run_budget_seconds=request["timeout"],
        max_tokens=3000,
    )
    try:
        if agent.tools:
            raise RuntimeError("tools_must_be_disabled")
        # Supported by this installed Hermes version: suppress session artifacts.
        agent._persist_disabled = True
        result = agent.run_conversation(json.dumps(request["context"], ensure_ascii=False))
        text = result.get("final_response")
        if not isinstance(text, str) or not text.strip() or result.get("error"):
            raise ValueError("parser_failed")
        return {"ok": True, "result": text}
    finally:
        agent.close()


def worker(request, temp):
    logging.disable(logging.CRITICAL)
    sys.path.insert(0, "/opt/hermes")
    os.chdir(temp)
    # All STT/provider scratch files live under the supervisor's cleanup scope.
    os.environ["TMPDIR"] = temp
    tempfile.tempdir = temp
    with (
        open(os.devnull, "w") as quiet,
        contextlib.redirect_stdout(quiet),
        contextlib.redirect_stderr(quiet),
    ):
        if request["mode"] == "transcribe":
            result = transcribe(request, Path(temp))
        elif request["mode"] == "parse":
            result = parse(request)
        else:
            raise ValueError("unknown_mode")
    print(json.dumps(result, ensure_ascii=False), flush=True)


def main():
    raw = sys.stdin.buffer.read(14_000_001)
    if len(raw) > 14_000_000:
        raise ValueError("input_too_large")
    request = json.loads(raw)
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        worker(request, sys.argv[2])
        return
    timeout = min(300, max(5, int(request["timeout"])))
    with tempfile.TemporaryDirectory(prefix="accounting-voice-") as temp:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--worker", temp],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            stdout, _ = process.communicate(raw, timeout=timeout)
        finally:
            # Also reap an STT grandchild if it outlived the worker.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        if process.returncode or len(stdout) > 30000:
            raise ValueError("worker_failed")
        sys.stdout.buffer.write(stdout)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print('{"ok":false,"error":"bridge_failed"}')
        sys.exit(1)
