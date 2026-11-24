"""Red-green TDD: multi-agent OTel RawEvent → curator tuples (Phase 4)."""

from __future__ import annotations

import unittest
from datetime import datetime

from curate.adapter_source_events import adapt_raw_event, iter_adapted_raw_events


def _env(agent: str, collector_id: str, raw: dict, ts: str = "2026-09-21T13:00:00+00:00"):
    return {
        "timestamp": ts,
        "collector_id": collector_id,
        "agent": agent,
        "raw_payload": raw,
    }


class OtelFlatAttrsTdd(unittest.TestCase):
    def test_claude_user_prompt(self) -> None:
        triple = adapt_raw_event(
            _env(
                "claude_code",
                "claude_code.otel",
                {
                    "resource": {
                        "attributes": [
                            {
                                "key": "service.name",
                                "value": {"stringValue": "claude-code"},
                            }
                        ]
                    },
                    "attributes": {
                        "event.name": "claude_code.user_prompt",
                        "session.id": "sess-cc",
                        "prompt_length": 12,
                    },
                },
            )
        )
        self.assertIsNotNone(triple)
        coll, fields, _ = triple
        self.assertEqual(coll, "otel")
        self.assertEqual(fields["tool"], "claude-code")
        self.assertEqual(fields["observable_type"], "user_prompt")
        self.assertEqual(fields["session_id"], "sess-cc")
        self.assertIsInstance(fields["occurred_at"], datetime)

    def test_codex_tool_call_flat_loki(self) -> None:
        triple = adapt_raw_event(
            _env(
                "codex",
                "codex.otel",
                {
                    "event.name": "codex.tool_call",
                    "service.name": "codex_tui",
                    "tool_name": "shell",
                    "conversation.id": "conv-cx",
                },
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["tool"], "codex")
        self.assertEqual(fields["observable_type"], "tool_request")
        self.assertEqual(fields["tool_name"], "shell")
        self.assertEqual(fields["session_id"], "conv-cx")

    def test_copilot_api_request_via_envelope_tool(self) -> None:
        triple = adapt_raw_event(
            _env(
                "copilot",
                "copilot.otel",
                {
                    "attributes": {
                        "event.name": "api_request",
                        "model": "gpt-4.1",
                        "input_tokens": 100,
                        "output_tokens": 20,
                    }
                },
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["tool"], "copilot")
        self.assertEqual(fields["observable_type"], "model_request")
        self.assertEqual(fields["input_tokens"], 100)


class OtelResourceLogsExpandTdd(unittest.TestCase):
    def test_expands_two_log_records(self) -> None:
        raw = {
            "resourceLogs": [
                {
                    "resource": {
                        "attributes": [
                            {
                                "key": "service.name",
                                "value": {"stringValue": "claude-code"},
                            }
                        ]
                    },
                    "scopeLogs": [
                        {
                            "logRecords": [
                                {
                                    "attributes": [
                                        {
                                            "key": "event.name",
                                            "value": {
                                                "stringValue": "user_prompt"
                                            },
                                        },
                                        {
                                            "key": "session.id",
                                            "value": {
                                                "stringValue": "s1"
                                            },
                                        },
                                    ]
                                },
                                {
                                    "attributes": [
                                        {
                                            "key": "event.name",
                                            "value": {
                                                "stringValue": "api_request"
                                            },
                                        },
                                        {
                                            "key": "session.id",
                                            "value": {
                                                "stringValue": "s1"
                                            },
                                        },
                                    ]
                                },
                            ]
                        }
                    ],
                }
            ]
        }
        triples = list(
            iter_adapted_raw_events(
                _env("claude_code", "claude_code.otel", raw)
            )
        )
        self.assertEqual(len(triples), 2)
        types = {t[1]["observable_type"] for t in triples}
        self.assertEqual(types, {"user_prompt", "model_request"})


class OtelSkipNoiseTdd(unittest.TestCase):
    def test_unknown_event_skipped(self) -> None:
        triple = adapt_raw_event(
            _env(
                "cursor",
                "cursor.otel",
                {"attributes": {"event.name": "heartbeat", "session.id": "x"}},
            )
        )
        self.assertIsNone(triple)

    def test_empty_attrs_skipped(self) -> None:
        triple = adapt_raw_event(
            _env("antigravity", "antigravity.otel", {"resource": {}})
        )
        self.assertIsNone(triple)


if __name__ == "__main__":
    unittest.main()
