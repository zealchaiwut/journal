"""Tests for generate_brief.py: OPEN_KEYS/CLOSED_KEYS loading + threading into
the prompt, and the empty/resolved_keys shape of empty_brief(). Run:
python -m unittest discover tests
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import generate_brief
from brief_schema import validate_brief

BANGKOK = timezone(timedelta(hours=7))


class _EnvVarTestCase(unittest.TestCase):
    """Helper: set/restore an env var for the duration of a test."""

    def _set_env(self, **kwargs):
        originals = {k: os.environ.get(k) for k in kwargs}
        os.environ.update(kwargs)

        def _restore():
            for k, v in originals.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        self.addCleanup(_restore)


class TestLoadOpenKeys(_EnvVarTestCase):
    def test_missing_file_returns_empty_list_no_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._set_env(HERMES_OPEN_KEYS_PATH=os.path.join(tmp, "does-not-exist.json"))
            self.assertEqual(generate_brief.load_open_keys(), [])

    def test_malformed_json_returns_empty_list_no_raise_and_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "open-keys.json")
            with open(path, "w") as f:
                f.write("{not valid json")
            self._set_env(HERMES_OPEN_KEYS_PATH=path)
            with self.assertLogs("generate_brief", level="WARNING"):
                result = generate_brief.load_open_keys()
            self.assertEqual(result, [])

    def test_valid_file_parsed_correctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "open-keys.json")
            payload = [{"key": "fix-widget-printer", "text": "fix the widget printer"}]
            with open(path, "w") as f:
                json.dump(payload, f)
            self._set_env(HERMES_OPEN_KEYS_PATH=path)
            self.assertEqual(generate_brief.load_open_keys(), payload)

    def test_non_list_json_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "open-keys.json")
            with open(path, "w") as f:
                json.dump({"key": "not-a-list"}, f)
            self._set_env(HERMES_OPEN_KEYS_PATH=path)
            self.assertEqual(generate_brief.load_open_keys(), [])


class TestLoadClosedKeys(_EnvVarTestCase):
    def test_missing_file_returns_empty_list_no_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._set_env(HERMES_CLOSED_KEYS_PATH=os.path.join(tmp, "does-not-exist.json"))
            self.assertEqual(generate_brief.load_closed_keys(), [])

    def test_malformed_json_returns_empty_list_no_raise_and_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "closed-keys.json")
            with open(path, "w") as f:
                f.write("[1, 2,")
            self._set_env(HERMES_CLOSED_KEYS_PATH=path)
            with self.assertLogs("generate_brief", level="WARNING"):
                result = generate_brief.load_closed_keys()
            self.assertEqual(result, [])

    def test_valid_file_parsed_correctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "closed-keys.json")
            payload = ["old-closed-task", "another-closed-task"]
            with open(path, "w") as f:
                json.dump(payload, f)
            self._set_env(HERMES_CLOSED_KEYS_PATH=path)
            self.assertEqual(generate_brief.load_closed_keys(), payload)


class TestBuildUserMessage(unittest.TestCase):
    def _base_args(self, open_keys, closed_keys):
        now = datetime(2026, 7, 14, 5, 45, tzinfo=BANGKOK)
        window = [("2026-07-14", "---\ndate: 2026-07-14\n---\nFixed the widget printer.")]
        source = generate_brief.build_source(window, "sonnet")
        return dict(
            now=now, window=window, prior_threads=[], source=source,
            boost_references="", open_keys=open_keys, closed_keys=closed_keys,
        )

    def test_open_and_closed_keys_sections_present_when_non_empty(self):
        open_keys = [{"key": "fix-widget-printer", "text": "fix the widget printer"}]
        closed_keys = ["old-closed-task"]
        msg = generate_brief.build_user_message(**self._base_args(open_keys, closed_keys))
        self.assertIn("OPEN_KEYS", msg)
        self.assertIn("CLOSED_KEYS", msg)
        self.assertIn("fix-widget-printer", msg)
        self.assertIn("old-closed-task", msg)

    def test_open_and_closed_keys_sections_present_but_empty(self):
        msg = generate_brief.build_user_message(**self._base_args([], []))
        # Sections/instructions must still appear even with nothing to report.
        self.assertIn("OPEN_KEYS", msg)
        self.assertIn("CLOSED_KEYS", msg)
        self.assertIn("reuse these exact keys", msg)
        self.assertIn("NEVER emit a todo whose key matches", msg)
        # And the empty list should be serialized, not silently dropped.
        self.assertIn("[]", msg)

    def test_does_not_crash_with_empty_window_and_keys(self):
        args = self._base_args([], [])
        args["window"] = []
        # Should not raise.
        generate_brief.build_user_message(**args)


class TestEmptyBriefResolvedKeys(unittest.TestCase):
    def test_empty_brief_includes_resolved_keys(self):
        now = datetime(2026, 7, 14, 5, 45, tzinfo=BANGKOK)
        brief = generate_brief.empty_brief(now, generate_brief.build_source([], "sonnet"))
        self.assertIn("resolved_keys", brief)
        self.assertEqual(brief["resolved_keys"], [])
        validate_brief(brief)


class TestGenerateRoundTrip(_EnvVarTestCase):
    """Confirm open_keys/closed_keys loaded from disk actually flow into the
    prompt that would be sent to `claude -p`, end to end through generate().
    """

    def test_open_closed_keys_threaded_into_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries_dir = os.path.join(tmp, "entries")
            os.makedirs(entries_dir)
            with open(os.path.join(entries_dir, "2026-07-14.md"), "w") as f:
                f.write(
                    "---\ndate: 2026-07-14\n---\n"
                    "## Good things\nFinally fixed the widget printer today.\n"
                    "## Concerns\nNeed to buy replacement toner soon.\n"
                )

            open_keys_path = os.path.join(tmp, "open-keys.json")
            closed_keys_path = os.path.join(tmp, "closed-keys.json")
            with open(open_keys_path, "w") as f:
                json.dump([{"key": "fix-widget-printer", "text": "fix the widget printer"}], f)
            with open(closed_keys_path, "w") as f:
                json.dump(["old-closed-task"], f)

            output_dir = os.path.join(tmp, "outputs")

            fake_brief_result = {
                "reflection": {
                    "title": "Journal reflection — Tue 14 Jul",
                    "markdown": "You finally fixed that widget printer today.",
                    "boost": "よし、次いこう。",
                    "word_count": 0,
                },
                "todos": [{
                    "id": "jrl-2026-07-14-01",
                    "content": "Buy replacement toner for the printer",
                    "key": "buy-printer-toner",
                    "category": "errands", "priority": "low",
                    "source_dates": ["2026-07-14"], "recurring": False,
                    "confidence": 0.8, "status": "pending", "origin": "journal",
                    "note": None,
                }],
                "threads": [],
                "resolved_keys": [{
                    "key": "fix-widget-printer",
                    "evidence": "entry says the widget printer got fixed today",
                }],
            }
            envelope = {"is_error": False, "result": json.dumps(fake_brief_result)}

            captured = {}

            def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
                captured["cmd"] = cmd
                captured["input"] = input
                return mock.Mock(returncode=0, stdout=json.dumps(envelope), stderr="")

            self._set_env(
                HERMES_OPEN_KEYS_PATH=open_keys_path,
                HERMES_CLOSED_KEYS_PATH=closed_keys_path,
                OUTPUT_DIR=output_dir,
            )

            with mock.patch.object(generate_brief, "ENTRIES_DIR", entries_dir), \
                 mock.patch.object(generate_brief.subprocess, "run", side_effect=fake_run):
                now = datetime(2026, 7, 14, 5, 45, tzinfo=BANGKOK)
                brief = generate_brief.generate(now, no_fetch=True)

            # The prompt actually sent to `claude -p` carries the loaded keys.
            self.assertIn("input", captured)
            self.assertIn("OPEN_KEYS", captured["input"])
            self.assertIn("fix-widget-printer", captured["input"])
            self.assertIn("CLOSED_KEYS", captured["input"])
            self.assertIn("old-closed-task", captured["input"])

            # And the resulting brief is valid + carries the resolved_keys
            # signal through untouched.
            validate_brief(brief)
            self.assertEqual(
                brief["resolved_keys"],
                [{"key": "fix-widget-printer",
                  "evidence": "entry says the widget printer got fixed today"}],
            )


if __name__ == "__main__":
    unittest.main()
