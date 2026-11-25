from dataclasses import replace
import json
import os
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from scripts.hermes_voice_bridge import parse
from voice.providers import HermesBridge, ProviderError
from voice.settings import VoiceSettings


class ModelSettingsTests(unittest.TestCase):
    def test_explicit_model_and_default_inheritance(self):
        env = {"VOICE_INPUT_ENABLED": "true", "VOICE_BRIDGE_COMMAND": '["bridge"]'}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(VoiceSettings.from_env().hermes_model, "")
            os.environ["HERMES_MODEL"] = "gpt-5.6-luna"
            self.assertEqual(VoiceSettings.from_env().hermes_model, "gpt-5.6-luna")

    def test_disabled_voice_needs_no_model_or_bridge(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(VoiceSettings.from_env().enabled)


class ParserModelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bridge = HermesBridge(replace(VoiceSettings(), hermes_model="gpt-5.6-luna"))
        self.answer = json.dumps({"operations": [], "clarification": "Какой бюджет?"})

    async def test_model_override_is_transport_config_not_transcript_data(self):
        self.bridge.call = AsyncMock(
            return_value={"ok": True, "result": self.answer, "model": "gpt-5.6-luna"}
        )
        self.assertEqual(await self.bridge.parse("Добавь 500"), self.answer)
        request = self.bridge.call.call_args.args[0]
        self.assertEqual(request["model"], "gpt-5.6-luna")
        self.assertNotIn("model", request["context"])
        self.bridge.call.return_value = {"ok": True, "transcript": "Добавь 500"}
        await self.bridge.transcribe(b"synthetic audio")
        self.assertNotIn("model", self.bridge.call.call_args.args[0])

    async def test_wrong_or_missing_model_is_rejected(self):
        for model in (None, "gpt-5.6-sol"):
            self.bridge.call = AsyncMock(
                return_value={"ok": True, "result": self.answer, "model": model}
            )
            with self.subTest(model=model), self.assertRaises(ProviderError):
                await self.bridge.parse("Добавь 500")


class ContainerModelTests(unittest.TestCase):
    def run_parser(self, requested, runtime, final_model=None):
        agent = Mock(tools=[])
        agent.run_conversation.return_value = {"final_response": "parsed"}

        def create_agent(**kwargs):
            agent.model = final_model or kwargs["model"]
            return agent

        factory = Mock(side_effect=create_agent)
        gateway = NS(
            _resolve_runtime_agent_kwargs=Mock(return_value=dict(runtime)),
            _resolve_gateway_model=Mock(return_value="gpt-5.6-sol"),
        )
        with patch.dict(sys.modules, {"gateway.run": gateway, "run_agent": NS(AIAgent=factory)}):
            try:
                result = parse(
                    {"model": requested, "instruction": "parse", "context": {}, "timeout": 180}
                )
            finally:
                agent.close.assert_called_once()
        self.assertEqual(factory.call_args.kwargs["enabled_toolsets"], [])
        return result

    def test_override_and_inheritance(self):
        for requested, runtime, expected in (
            ("gpt-5.6-luna", {"model": "gpt-5.6-sol"}, "gpt-5.6-luna"),
            ("", {"model": "runtime-model"}, "runtime-model"),
            ("", {}, "gpt-5.6-sol"),
        ):
            with self.subTest(requested=requested, runtime=runtime):
                self.assertEqual(self.run_parser(requested, runtime)["model"], expected)

    def test_changed_model_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "parser_model_mismatch"):
            self.run_parser("gpt-5.6-luna", {}, final_model="gpt-5.6-sol")
