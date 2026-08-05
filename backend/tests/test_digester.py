import unittest
from unittest.mock import AsyncMock, patch

from backend import config
from backend.pipeline import digester


class ChatCompletionsUrlTests(unittest.TestCase):
    def test_a_provider_host_is_completed_to_the_chat_route(self):
        # Provider rows only need the gateway host for the Agent SDK backend.
        self.assertEqual(
            digester._chat_completions_url("http://oneapi.example"),
            "http://oneapi.example/v1/chat/completions",
        )
        self.assertEqual(
            digester._chat_completions_url("http://oneapi.example/v1"),
            "http://oneapi.example/v1/chat/completions",
        )

    def test_a_complete_url_is_left_alone(self):
        self.assertEqual(
            digester._chat_completions_url("http://oneapi.example/v1/chat/completions"),
            "http://oneapi.example/v1/chat/completions",
        )

    def test_a_gateway_subpath_keeps_its_prefix(self):
        self.assertEqual(
            digester._chat_completions_url("https://api.deepseek.com/anthropic"),
            "https://api.deepseek.com/anthropic/v1/chat/completions",
        )


class ChatDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_failed_sdk_call_falls_back_to_the_http_client(self):
        agent_complete = AsyncMock(side_effect=RuntimeError("claude CLI exited 1"))
        chat_http = AsyncMock(return_value="summary")

        with (
            patch.object(config, "AI_BACKEND", "agent_sdk"),
            patch.object(config, "AI_HTTP_FALLBACK", True),
            patch("backend.pipeline.agent.agent_complete", agent_complete),
            patch.object(digester, "_chat_http", chat_http),
        ):
            result = await digester._chat("system", "content", endpoint="http://x/v1")

        self.assertEqual(result, "summary")
        self.assertEqual(chat_http.await_count, 1)

    async def test_the_fallback_can_be_switched_off(self):
        agent_complete = AsyncMock(side_effect=RuntimeError("claude CLI exited 1"))
        chat_http = AsyncMock(return_value="summary")

        with (
            patch.object(config, "AI_BACKEND", "agent_sdk"),
            patch.object(config, "AI_HTTP_FALLBACK", False),
            patch("backend.pipeline.agent.agent_complete", agent_complete),
            patch.object(digester, "_chat_http", chat_http),
        ):
            with self.assertRaisesRegex(RuntimeError, "claude CLI exited 1"):
                await digester._chat("system", "content", endpoint="http://x/v1")

        self.assertEqual(chat_http.await_count, 0)


if __name__ == "__main__":
    unittest.main()
