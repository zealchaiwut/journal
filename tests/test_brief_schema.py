"""Contract tests: validator, normalization (dedupe / confidence / status
mapping), atomic write, empty-window brief. Run: python -m unittest discover tests
"""

import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brief_schema import BriefValidationError, normalize_brief, validate_brief

BANGKOK = timezone(timedelta(hours=7))

VALID = {
    "schema_version": "1.2",
    "for_date": "2026-07-14",
    "generated_at": "2026-07-14T05:45:00+07:00",
    "source": {"engine": "claude -p --model sonnet", "latest_entry": "2026-07-13",
               "window": ["2026-07-13", "2026-07-12"], "entry_count": 2},
    "reflection": {"title": "Journal reflection — Tue 14 Jul",
                   "markdown": "You kept the streak going.",
                   "boost": "その調子でいいんだよ、俺が見てるから。",
                   "word_count": 5},
    "todos": [{
        "id": "jrl-2026-07-14-01", "content": "Set up 2 BCG mock case trials",
        "key": "bcg-mock-cases",
        "category": "bcg", "priority": "high", "source_dates": ["2026-07-13"],
        "recurring": False, "confidence": 0.92, "status": "pending",
        "origin": "journal", "note": None,
    }],
    "threads": [{"key": "cashflow", "label": "Cashflow / money",
                 "first_seen": "2026-06-29", "days_active": 10,
                 "sentiment": "worry", "note": "recurring"}],
    "resolved_keys": [],
}


class TestValidate(unittest.TestCase):
    def test_valid_brief_passes(self):
        validate_brief(copy.deepcopy(VALID))

    def test_empty_lists_pass(self):
        b = copy.deepcopy(VALID)
        b["todos"], b["threads"] = [], []
        validate_brief(b)

    def _expect_fail(self, mutate):
        b = copy.deepcopy(VALID)
        mutate(b)
        with self.assertRaises(BriefValidationError):
            validate_brief(b)

    def test_rejects_bad_schema_version(self):
        self._expect_fail(lambda b: b.update(schema_version="2.0"))

    def test_rejects_schema_version_1_1(self):
        self._expect_fail(lambda b: b.update(schema_version="1.1"))

    def test_rejects_schema_version_1_0(self):
        self._expect_fail(lambda b: b.update(schema_version="1.0"))

    def test_rejects_missing_todo_key(self):
        self._expect_fail(lambda b: b["todos"][0].pop("key"))

    def test_rejects_uppercase_todo_key(self):
        self._expect_fail(lambda b: b["todos"][0].update(key="BCG-Mock"))

    def test_rejects_todo_key_with_spaces(self):
        self._expect_fail(lambda b: b["todos"][0].update(key="bcg mock cases"))

    def test_rejects_todo_key_with_leading_hyphen(self):
        self._expect_fail(lambda b: b["todos"][0].update(key="-bcg-mock"))

    def test_rejects_todo_key_with_trailing_hyphen(self):
        self._expect_fail(lambda b: b["todos"][0].update(key="bcg-mock-"))

    def test_rejects_todo_key_with_doubled_hyphen(self):
        self._expect_fail(lambda b: b["todos"][0].update(key="bcg--mock"))

    def test_rejects_empty_todo_key(self):
        self._expect_fail(lambda b: b["todos"][0].update(key=""))

    def test_accepts_single_word_todo_key(self):
        b = copy.deepcopy(VALID)
        b["todos"][0]["key"] = "dentist"
        validate_brief(b)

    def test_accepts_multi_hyphen_todo_key(self):
        b = copy.deepcopy(VALID)
        b["todos"][0]["key"] = "fix-widget-printer-again"
        validate_brief(b)

    def test_rejects_unknown_category(self):
        self._expect_fail(lambda b: b["todos"][0].update(category="sailing"))

    def test_rejects_unknown_priority(self):
        self._expect_fail(lambda b: b["todos"][0].update(priority="urgent"))

    def test_rejects_open_status(self):
        self._expect_fail(lambda b: b["todos"][0].update(status="open"))

    def test_rejects_confidence_out_of_range(self):
        self._expect_fail(lambda b: b["todos"][0].update(confidence=1.5))

    def test_rejects_bad_sentiment(self):
        self._expect_fail(lambda b: b["threads"][0].update(sentiment="angry"))

    def test_rejects_bad_date(self):
        self._expect_fail(lambda b: b.update(for_date="14/07/2026"))

    def test_rejects_bad_due_date(self):
        self._expect_fail(lambda b: b["todos"][0].update(due="soon"))

    def test_rejects_missing_boost(self):
        self._expect_fail(lambda b: b["reflection"].pop("boost"))

    def test_rejects_empty_boost(self):
        self._expect_fail(lambda b: b["reflection"].update(boost="  "))


class TestNormalize(unittest.TestCase):
    def test_text_maps_to_content(self):
        b = copy.deepcopy(VALID)
        t = b["todos"][0]
        t["text"] = t.pop("content")
        out = normalize_brief(b)
        self.assertEqual(out["todos"][0]["content"], "Set up 2 BCG mock case trials")
        self.assertNotIn("text", out["todos"][0])

    def test_open_status_maps_to_pending(self):
        b = copy.deepcopy(VALID)
        b["todos"][0]["status"] = "open"
        self.assertEqual(normalize_brief(b)["todos"][0]["status"], "pending")

    def test_low_confidence_dropped(self):
        b = copy.deepcopy(VALID)
        weak = copy.deepcopy(b["todos"][0])
        weak.update(id="jrl-2026-07-14-02", confidence=0.3)
        b["todos"].append(weak)
        self.assertEqual(len(normalize_brief(b)["todos"]), 1)

    def test_duplicate_ids_deduped(self):
        b = copy.deepcopy(VALID)
        b["todos"].append(copy.deepcopy(b["todos"][0]))
        self.assertEqual(len(normalize_brief(b)["todos"]), 1)

    def test_word_count_recomputed(self):
        b = copy.deepcopy(VALID)
        b["reflection"]["word_count"] = 9999
        self.assertEqual(normalize_brief(b)["reflection"]["word_count"], 5)

    def test_normalized_valid_roundtrip(self):
        b = copy.deepcopy(VALID)
        b["todos"][0]["status"] = "open"
        validate_brief(normalize_brief(b))

    def test_missing_resolved_keys_normalizes_to_empty_list(self):
        b = copy.deepcopy(VALID)
        del b["resolved_keys"]
        out = normalize_brief(b)
        self.assertEqual(out["resolved_keys"], [])
        validate_brief(out)

    def test_null_resolved_keys_normalizes_to_empty_list(self):
        b = copy.deepcopy(VALID)
        b["resolved_keys"] = None
        out = normalize_brief(b)
        self.assertEqual(out["resolved_keys"], [])


class TestResolvedKeys(unittest.TestCase):
    def test_valid_entries_pass(self):
        b = copy.deepcopy(VALID)
        b["resolved_keys"] = [
            {"key": "dentist-visit", "evidence": "entry says the appointment happened"},
        ]
        validate_brief(b)

    def test_not_a_list_fails(self):
        b = copy.deepcopy(VALID)
        b["resolved_keys"] = {"key": "dentist-visit", "evidence": "done"}
        with self.assertRaises(BriefValidationError):
            validate_brief(b)

    def test_entry_missing_key_fails(self):
        b = copy.deepcopy(VALID)
        b["resolved_keys"] = [{"evidence": "entry says it happened"}]
        with self.assertRaises(BriefValidationError):
            validate_brief(b)

    def test_entry_missing_evidence_fails(self):
        b = copy.deepcopy(VALID)
        b["resolved_keys"] = [{"key": "dentist-visit"}]
        with self.assertRaises(BriefValidationError):
            validate_brief(b)

    def test_entry_empty_key_fails(self):
        b = copy.deepcopy(VALID)
        b["resolved_keys"] = [{"key": "", "evidence": "entry says it happened"}]
        with self.assertRaises(BriefValidationError):
            validate_brief(b)

    def test_entry_empty_evidence_fails(self):
        b = copy.deepcopy(VALID)
        b["resolved_keys"] = [{"key": "dentist-visit", "evidence": "   "}]
        with self.assertRaises(BriefValidationError):
            validate_brief(b)


class TestOutputs(unittest.TestCase):
    def test_atomic_write_and_empty_brief(self):
        import generate_brief
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OUTPUT_DIR"] = tmp
            try:
                now = datetime(2026, 7, 14, 5, 45, tzinfo=BANGKOK)
                brief = generate_brief.empty_brief(
                    now, generate_brief.build_source([], "sonnet"))
                self.assertEqual(brief["resolved_keys"], [])
                validate_brief(brief)
                latest = generate_brief.write_outputs(brief)
                with open(latest) as f:
                    written = json.load(f)
                self.assertEqual(written["for_date"], "2026-07-14")
                self.assertEqual(written["resolved_keys"], [])
                self.assertTrue(os.path.exists(
                    os.path.join(tmp, "2026-07-14.journal_brief.json")))
                self.assertEqual(
                    [n for n in os.listdir(tmp) if n.endswith(".tmp")], [])
            finally:
                del os.environ["OUTPUT_DIR"]


if __name__ == "__main__":
    unittest.main()
