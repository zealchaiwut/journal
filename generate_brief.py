#!/usr/bin/env python3
"""Generate the Hermes morning-brief JSON from recent journal entries.

Smart-server step: (optionally) run the email fetch, read the last N dated
entries plus yesterday's threads, ask Claude (via `claude -p`) for a
reflection + extracted to-dos, validate against the contract
(docs/hermes_journal_brief.contract.md), and atomically publish
outputs/hermes/journal_brief.latest.json plus a dated copy. Hermes only
reads that file — all intelligence lives here.

Usage:
  python generate_brief.py              # fetch -> reflect -> write
  python generate_brief.py --no-fetch   # skip email step, regenerate only
  python generate_brief.py --dry-run    # print JSON to stdout, write nothing
"""

import argparse
import glob
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta

from brief_schema import (
    CATEGORIES, BriefValidationError, normalize_brief, validate_brief,
)
from journal_fetch import load_env, ENTRIES_DIR, REPO_DIR, BANGKOK

PROMPT_PATH = os.path.join(REPO_DIR, "prompts", "journal_reflection.md")
BOOST_REFERENCES_PATH = os.path.join(REPO_DIR, "prompts", "boost_references.md")

log = logging.getLogger("generate_brief")


def _cfg(name: str, default: str) -> str:
    return os.environ.get(name, default)


def output_dir() -> str:
    path = _cfg("OUTPUT_DIR", "outputs/hermes")
    return path if os.path.isabs(path) else os.path.join(REPO_DIR, path)


def hermes_open_keys_path() -> str:
    # Hermes is expected to export this file before journal's scheduled run
    # (via a small CLI or file export on the Hermes side — the actual
    # cross-repo wiring is deploy-time config, not this repo's concern).
    # Shape: [{"key": "...", "text": "..."}, ...].
    return os.path.expanduser(
        _cfg("HERMES_OPEN_KEYS_PATH", "~/.hermes/contracts/todo-open-keys.json"))


def hermes_closed_keys_path() -> str:
    # Same export expectation as hermes_open_keys_path(). Shape: a flat list
    # of key strings, e.g. ["key1", "key2", ...].
    return os.path.expanduser(
        _cfg("HERMES_CLOSED_KEYS_PATH", "~/.hermes/contracts/todo-closed-keys.json"))


def run_fetch() -> None:
    """Best-effort email fetch — a failure never blocks the brief."""
    try:
        result = subprocess.run(
            [sys.executable, os.path.join(REPO_DIR, "journal_fetch.py")],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            log.warning("journal_fetch failed (rc=%d): %s",
                        result.returncode, result.stderr.strip()[-500:])
    except Exception as e:
        log.warning("journal_fetch step errored: %s", e)


def load_window(window_size: int) -> list[tuple[str, str]]:
    """Return [(date, entry_text)] for the newest `window_size` entries."""
    paths = sorted(glob.glob(os.path.join(ENTRIES_DIR, "????-??-??.md")), reverse=True)
    window = []
    for path in paths[:window_size]:
        entry_date = os.path.splitext(os.path.basename(path))[0]
        with open(path) as f:
            window.append((entry_date, f.read()))
    return window


def load_boost_references() -> str:
    if not os.path.exists(BOOST_REFERENCES_PATH):
        return ""
    with open(BOOST_REFERENCES_PATH) as f:
        return f.read()


def load_prior_threads() -> list:
    latest = os.path.join(output_dir(), "journal_brief.latest.json")
    if not os.path.exists(latest):
        return []
    try:
        with open(latest) as f:
            return json.load(f).get("threads", [])
    except Exception as e:
        log.warning("Could not read prior threads: %s", e)
        return []


def load_open_keys() -> list:
    """Hermes's open todo keys: [{"key": "...", "text": "..."}, ...], or []."""
    path = hermes_open_keys_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        log.warning("Could not read Hermes open keys: %s", e)
        return []


def load_closed_keys() -> list:
    """Hermes's closed todo keys: ["key1", "key2", ...], or []."""
    path = hermes_closed_keys_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        log.warning("Could not read Hermes closed keys: %s", e)
        return []


def build_source(window: list[tuple[str, str]], model: str) -> dict:
    return {
        "engine": f"claude -p --model {model}",
        "latest_entry": window[0][0] if window else None,
        "window": [d for d, _ in window],
        "entry_count": len(window),
    }


def empty_brief(now: datetime, source: dict) -> dict:
    """Valid brief for a fresh setup with no entries — never crash Hermes."""
    return {
        "schema_version": "1.2",
        "for_date": now.date().isoformat(),
        "generated_at": now.isoformat(timespec="seconds"),
        "source": source,
        "reflection": {
            "title": f"Journal reflection — {now.strftime('%a %d %b')}",
            "markdown": (
                "- No journal entries in the window yet.\n"
                "- Once tonight's page lands from the Kindle Scribe, "
                "tomorrow's brief will have a real reflection here.\n"
                "- A blank page today just means the habit is about to start."
            ),
            "boost": "何もない日から始まる。それでいいんだよ。",
            "word_count": 0,
        },
        "todos": [],
        "threads": [],
        "resolved_keys": [],
    }


def build_user_message(now: datetime, window, prior_threads, source, boost_references,
                        open_keys, closed_keys) -> str:
    shape = {
        "schema_version": "1.2",
        "for_date": now.date().isoformat(),
        "generated_at": now.isoformat(timespec="seconds"),
        "source": source,
        "reflection": {
            "title": "...", "markdown": "...",
            "boost": "<ONE Japanese sentence, spoken register, see BOOST rules>",
            "word_count": 0,
        },
        "todos": [{
            "id": f"jrl-{now.date().isoformat()}-01",
            "content": "<cleaned imperative task — NOTE: field is `content`, not `text`>",
            "key": "<stable-slug-see-rules>",
            "category": "<one of the enum>", "priority": "high|medium|low",
            "source_dates": ["YYYY-MM-DD"], "recurring": False,
            "confidence": 0.9, "status": "pending", "origin": "journal",
            "note": None,
        }],
        "threads": [{
            "key": "...", "label": "...", "first_seen": "YYYY-MM-DD",
            "days_active": 1, "sentiment": "positive|worry|tension|neutral",
            "note": "...",
        }],
        "resolved_keys": [{
            "key": "<the OPEN_KEYS key that was resolved>",
            "evidence": "<short paraphrase of what the journal text said>",
        }],
    }
    entries_text = "\n\n".join(
        f"=== ENTRY {d} ===\n{text.strip()}" for d, text in window
    )
    return (
        f"FOR_DATE: {shape['for_date']}\n"
        f"GENERATED_AT: {shape['generated_at']}\n"
        f"CATEGORY_ENUM: {json.dumps(sorted(CATEGORIES))}\n"
        "TODO STATUS: always the string \"pending\" (Hermes todo_tool schema; "
        "use field name `content` for the task text).\n"
        f"EXACT OUTPUT SHAPE (fill reflection/todos/threads/resolved_keys, keep the rest "
        f"verbatim):\n"
        f"{json.dumps(shape, indent=1)}\n\n"
        f"PRIOR_THREADS: {json.dumps(prior_threads)}\n\n"
        f"OPEN_KEYS (reuse these exact keys if a task recurs — do not mint a new key for the "
        f"same task):\n\n{json.dumps(open_keys, ensure_ascii=False)}\n\n"
        f"CLOSED_KEYS (NEVER emit a todo whose key matches one of these, even if the task is "
        f"mentioned again in the journal text):\n\n{json.dumps(closed_keys, ensure_ascii=False)}\n\n"
        f"REFERENCE_QUOTES (calibrate boost tone/intensity from these — see BOOST rules; "
        f"never copy verbatim):\n\n{boost_references}\n\n"
        f"JOURNAL ENTRIES (newest first):\n\n{entries_text}"
    )


def call_claude(system_prompt: str, user_message: str, model: str) -> dict:
    """Run `claude -p` and return the parsed JSON object the model emitted."""
    result = subprocess.run(
        ["claude", "-p", "--model", model, "--output-format", "json"],
        input=f"{system_prompt}\n\n{user_message}",
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        dump = os.path.join(REPO_DIR, "logs", "claude_failure.json")
        os.makedirs(os.path.dirname(dump), exist_ok=True)
        with open(dump, "w") as f:
            f.write(result.stdout + "\n--- stderr ---\n" + result.stderr)
        raise RuntimeError(
            f"claude -p failed (rc={result.returncode}), full output in {dump}: "
            f"stderr={result.stderr.strip()[-200:]!r}"
        )
    envelope = json.loads(result.stdout)
    if envelope.get("is_error"):
        raise RuntimeError(f"claude -p returned error: {envelope.get('result')!r}")
    text = envelope.get("result", "")
    # Belt and braces: strip markdown fences if the model ignored instructions.
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    return json.loads(text)


def write_outputs(brief: dict) -> str:
    out_dir = output_dir()
    os.makedirs(out_dir, exist_ok=True)
    payload = json.dumps(brief, ensure_ascii=False, indent=1)

    dated = os.path.join(out_dir, f"{brief['for_date']}.journal_brief.json")
    with open(dated, "w") as f:
        f.write(payload)

    latest = os.path.join(out_dir, "journal_brief.latest.json")
    fd, tmp = tempfile.mkstemp(dir=out_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.chmod(tmp, 0o644)  # mkstemp defaults to 0600
        os.replace(tmp, latest)  # atomic: Hermes never sees a partial file
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return latest


def generate(now: datetime, no_fetch: bool) -> dict:
    model = _cfg("MODEL", "sonnet")
    window_size = int(_cfg("WINDOW_SIZE", "10"))

    if not no_fetch:
        run_fetch()

    window = load_window(window_size)
    source = build_source(window, model)
    if not window:
        log.warning("No entries in window — emitting empty brief")
        return empty_brief(now, source)

    with open(PROMPT_PATH) as f:
        system_prompt = f.read()
    user_message = build_user_message(
        now, window, load_prior_threads(), source, load_boost_references(),
        load_open_keys(), load_closed_keys())

    last_error = None
    for attempt in (1, 2):
        try:
            brief = call_claude(system_prompt, user_message, model)
            # Caller-owned fields are authoritative — stamp over model output.
            brief["schema_version"] = "1.2"
            brief["for_date"] = now.date().isoformat()
            brief["generated_at"] = now.isoformat(timespec="seconds")
            brief["source"] = source
            return validate_brief(normalize_brief(brief))
        except (json.JSONDecodeError, BriefValidationError, RuntimeError) as e:
            last_error = e
            log.warning("Attempt %d failed: %s", attempt, e)
    raise SystemExit(f"Brief generation failed twice; keeping previous file. "
                     f"Last error: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the JSON to stdout, write nothing")
    parser.add_argument("--no-fetch", action="store_true",
                        help="skip the email fetch step")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    load_env()

    brief = generate(datetime.now(BANGKOK), no_fetch=args.no_fetch)
    if args.dry_run:
        print(json.dumps(brief, ensure_ascii=False, indent=1))
        return 0
    latest = write_outputs(brief)
    log.info("Brief published: %s (%d todos, %d threads)",
             latest, len(brief["todos"]), len(brief["threads"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
