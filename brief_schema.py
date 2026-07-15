"""Validation + normalization for the journal -> Hermes brief contract (v1.1).

Authoritative contract: docs/hermes_journal_brief.contract.md. Field names on
todo items follow Hermes's native todo_tool schema (id / content / status,
status "pending"), so Hermes can merge with a plain concat+dedupe. The LLM may
emit `text` / status "open" per its prompt — normalize_brief() maps both.
"""

import re
from datetime import date

CATEGORIES = {
    "bcg", "maguro", "perf-coach", "hermes", "trip", "finance",
    "health", "running", "cooking", "errands", "general",
}
PRIORITIES = {"high", "medium", "low"}
SENTIMENTS = {"positive", "worry", "tension", "neutral"}
# Hermes todo_tool status enum; journal only ever emits "pending".
TODO_STATUS = "pending"
MIN_CONFIDENCE = 0.4

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class BriefValidationError(ValueError):
    pass


def _err(msg: str):
    raise BriefValidationError(msg)


def _check_iso_date(value, where: str) -> str:
    if not isinstance(value, str) or not _ISO_DATE.match(value):
        _err(f"{where}: not an ISO date: {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError:
        _err(f"{where}: invalid date: {value!r}")
    return value


def normalize_brief(brief: dict) -> dict:
    """Map LLM output onto the contract before validation.

    - todo `text` -> `content`; status "open"/missing -> "pending"
    - drop todos under MIN_CONFIDENCE or with duplicate ids
    - recompute reflection.word_count
    """
    if not isinstance(brief, dict):
        _err("brief is not an object")

    todos = brief.get("todos") or []
    seen_ids = set()
    normalized = []
    for t in todos:
        if not isinstance(t, dict):
            continue
        if "content" not in t and "text" in t:
            t["content"] = t.pop("text")
        status = str(t.get("status", "")).strip().lower()
        if status in ("", "open", "pending"):
            t["status"] = TODO_STATUS
        conf = t.get("confidence")
        if isinstance(conf, (int, float)) and conf < MIN_CONFIDENCE:
            continue
        tid = t.get("id")
        if tid in seen_ids:
            continue
        seen_ids.add(tid)
        normalized.append(t)
    brief["todos"] = normalized

    reflection = brief.get("reflection")
    if isinstance(reflection, dict) and isinstance(reflection.get("markdown"), str):
        reflection["word_count"] = len(reflection["markdown"].split())
    return brief


def validate_brief(brief: dict) -> dict:
    """Raise BriefValidationError unless `brief` satisfies contract v1.0."""
    if not isinstance(brief, dict):
        _err("brief is not an object")
    if brief.get("schema_version") != "1.1":
        _err(f"schema_version must be '1.1', got {brief.get('schema_version')!r}")
    _check_iso_date(brief.get("for_date"), "for_date")
    if not isinstance(brief.get("generated_at"), str) or "T" not in brief["generated_at"]:
        _err("generated_at must be an ISO8601 timestamp string")

    source = brief.get("source")
    if not isinstance(source, dict):
        _err("source must be an object")
    if not isinstance(source.get("entry_count"), int) or source["entry_count"] < 0:
        _err("source.entry_count must be a non-negative int")
    if not isinstance(source.get("window"), list):
        _err("source.window must be a list")
    for d in source["window"]:
        _check_iso_date(d, "source.window")

    reflection = brief.get("reflection")
    if not isinstance(reflection, dict):
        _err("reflection must be an object")
    if not isinstance(reflection.get("title"), str) or not reflection["title"].strip():
        _err("reflection.title must be a non-empty string")
    if not isinstance(reflection.get("markdown"), str) or not reflection["markdown"].strip():
        _err("reflection.markdown must be a non-empty string")
    if not isinstance(reflection.get("boost"), str) or not reflection["boost"].strip():
        _err("reflection.boost must be a non-empty string")
    if not isinstance(reflection.get("word_count"), int):
        _err("reflection.word_count must be an int")

    todos = brief.get("todos")
    if not isinstance(todos, list):
        _err("todos must be a list")
    for i, t in enumerate(todos):
        where = f"todos[{i}]"
        if not isinstance(t, dict):
            _err(f"{where}: not an object")
        if not isinstance(t.get("id"), str) or not t["id"].strip():
            _err(f"{where}.id must be a non-empty string")
        if not isinstance(t.get("content"), str) or not t["content"].strip():
            _err(f"{where}.content must be a non-empty string")
        if t.get("status") != TODO_STATUS:
            _err(f"{where}.status must be {TODO_STATUS!r}, got {t.get('status')!r}")
        if t.get("category") not in CATEGORIES:
            _err(f"{where}.category invalid: {t.get('category')!r}")
        if t.get("priority") not in PRIORITIES:
            _err(f"{where}.priority invalid: {t.get('priority')!r}")
        conf = t.get("confidence")
        if not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
            _err(f"{where}.confidence must be a number in [0,1]")
        if not isinstance(t.get("source_dates"), list) or not t["source_dates"]:
            _err(f"{where}.source_dates must be a non-empty list")
        for d in t["source_dates"]:
            _check_iso_date(d, f"{where}.source_dates")
        if not isinstance(t.get("recurring"), bool):
            _err(f"{where}.recurring must be a bool")
        if t.get("origin") != "journal":
            _err(f"{where}.origin must be 'journal'")
        for opt in ("due", "defer_until"):
            if t.get(opt) is not None and opt in t:
                _check_iso_date(t[opt], f"{where}.{opt}")

    threads = brief.get("threads")
    if not isinstance(threads, list):
        _err("threads must be a list")
    for i, th in enumerate(threads):
        where = f"threads[{i}]"
        if not isinstance(th, dict):
            _err(f"{where}: not an object")
        for field in ("key", "label"):
            if not isinstance(th.get(field), str) or not th[field].strip():
                _err(f"{where}.{field} must be a non-empty string")
        _check_iso_date(th.get("first_seen"), f"{where}.first_seen")
        if not isinstance(th.get("days_active"), int) or th["days_active"] < 0:
            _err(f"{where}.days_active must be a non-negative int")
        if th.get("sentiment") not in SENTIMENTS:
            _err(f"{where}.sentiment invalid: {th.get('sentiment')!r}")

    return brief
