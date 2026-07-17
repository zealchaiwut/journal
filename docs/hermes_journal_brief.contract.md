# Journal → Hermes brief contract (schema v1.2)

Interface between the **journal smart server** (this repo, runs on the mac mini)
and the **Hermes lean client**. One JSON file:

- `outputs/hermes/journal_brief.latest.json` — atomically replaced each morning
  (temp file + `os.replace`); Hermes reads only this.
- `outputs/hermes/YYYY-MM-DD.journal_brief.json` — dated history copies.

Hermes pastes `reflection.markdown` into the morning brief and merges `todos`
into its own list (plain concat + dedupe by `id`). Hermes does no LLM work.

## Shape

```json
{
  "schema_version": "1.2",
  "for_date": "2026-07-14",
  "generated_at": "2026-07-14T05:45:00+07:00",
  "source": {
    "engine": "claude -p --model sonnet",
    "latest_entry": "2026-07-13",
    "window": ["2026-07-13", "..."],
    "entry_count": 11
  },
  "reflection": {
    "title": "Journal reflection — Tue 14 Jul",
    "markdown": "<ready-to-render: 5–8 one-line bullets, final bullet = today's nudge>",
    "boost": "<ONE Japanese sentence, spoken register, grounded in today's entry>",
    "word_count": 180
  },
  "todos": [
    {
      "id": "jrl-2026-07-14-01",
      "content": "Set up 2 BCG mock case trials with Claude",
      "key": "bcg-mock-cases",
      "status": "pending",
      "category": "bcg",
      "priority": "high",
      "source_dates": ["2026-07-13"],
      "recurring": false,
      "confidence": 0.92,
      "origin": "journal",
      "note": null
    }
  ],
  "threads": [
    {
      "key": "cashflow",
      "label": "Cashflow / money",
      "first_seen": "2026-06-29",
      "days_active": 10,
      "sentiment": "worry",
      "note": "..."
    }
  ],
  "resolved_keys": [
    {
      "key": "dentist-visit",
      "evidence": "entry says the dentist appointment happened this week"
    }
  ]
}
```

## Field rules

| Field | Rule |
|-------|------|
| `reflection.boost` | one Japanese sentence, spoken register (セリフ) — not a translation of `markdown`, not motivational-poster copy; grounded in something concrete from today's entry |
| `todos[].id` | unique, `jrl-<for_date>-NN` |
| `todos[].content` | task text — **Hermes todo_tool field name** (not `text`) |
| `todos[].key` | content-derived slug, stable across days for the same task (lowercase, hyphenated, 2-3 words, no dates, e.g. `dentist-visit`). Reused from `OPEN_KEYS` when the task recurs; never a `CLOSED_KEYS` duplicate. Server validates the slug pattern (`^[a-z0-9]+(-[a-z0-9]+)*$`) |
| `todos[].status` | always `"pending"` — Hermes enum: pending / in_progress / completed / cancelled |
| `todos[].category` | `bcg maguro perf-coach hermes trip finance health running cooking errands general` |
| `todos[].priority` | `high medium low` — **urgency/deadline/blocking-ness only.** Recurrence does NOT raise priority — a task appearing on many unresolved days is `recurring: true`, not automatically `high`. Advisory; Hermes todo_tool has no priority field (list order = priority), so Hermes should insert high→top |
| `todos[].confidence` | 0–1; server drops anything < 0.4 before publishing |
| `todos[].due` / `defer_until` | optional ISO dates; omitted unless the journal named one |
| `threads[].sentiment` | `positive worry tension neutral` |
| `threads` | 2–5 arcs, merged day-over-day by `key` (prior day's threads are fed back to the model) |
| `resolved_keys` | array of `{key, evidence}`; always present (may be empty). Populated when `OPEN_KEYS` names a task this window's entries describe as done — the task is dropped from `todos[]` and surfaced here instead so Hermes can close the row |
| `resolved_keys[].key` | must match a key from the `OPEN_KEYS` input (see below), never invented |
| `resolved_keys[].evidence` | short paraphrase of what the journal text said, for audit |

## Hermes round-trip inputs (OPEN_KEYS / CLOSED_KEYS)

For todo keys to stay stable across days, Hermes's persistent todo store is
the source of truth for which keys already exist and which are already
closed. Before each scheduled run, Hermes is expected to export two plain
JSON files that this repo reads (mirrors the existing `PRIOR_THREADS`
round-trip, which is internal to this repo and not written by Hermes):

- `HERMES_OPEN_KEYS_PATH` (default `~/.hermes/contracts/todo-open-keys.json`)
  — `[{"key": "...", "text": "..."}, ...]`, from
  `todo_store.get_open_keys()` in the hermes-agent repo.
- `HERMES_CLOSED_KEYS_PATH` (default `~/.hermes/contracts/todo-closed-keys.json`)
  — `["key1", "key2", ...]`, from `todo_store.get_closed_keys()`.

Both paths are env-var configurable (see `.env.example`). If a file is
missing or malformed, this repo treats it as `[]` and proceeds — it never
blocks brief generation. The actual export mechanism (small CLI vs. file
write) is Hermes-side, deploy-time wiring and is not specified by this
contract.

`resolved_keys` (see above) is the signal flowing the other way: a later
Hermes-side integration reads it and calls
`todo_store.close_todo(key, "done", source="journal:resolution")` for each
entry. This repo only produces the signal; consuming it is out of scope here.

## Hermes-side merge (reference)

```
new = [t for t in brief.todos if t.id not in existing_ids]
todo_tool.write([{id, content, status} for t in new], merge=True)
```

Extra fields (`category`, `priority`, `confidence`, …) are metadata Hermes may
use for ordering/labels or ignore entirely.

**Known gap (TODO):** Commander's REST todos (`/api/projects/{p}/todos`) use
`{id, text, done, position}` — a different schema. This contract targets the
hermes-agent-fork `todo_tool` (`{id, content, status}`). If briefs should land
in Commander todos instead, add a thin translation on the Hermes side —
do not change this contract.

## Failure semantics

- Generation failing twice → the server exits non-zero and **does not touch**
  `journal_brief.latest.json`; Hermes serves yesterday's file.
- Empty entry window → valid file with `todos: []`, `threads: []`, friendly
  `reflection.markdown`. Hermes never needs a special case.
- No new mail → brief still regenerates over the existing window.
