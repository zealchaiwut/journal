# Journal → Hermes brief contract (schema v1.0)

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
  "schema_version": "1.0",
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
    "markdown": "<ready-to-render, 150–220 words>",
    "word_count": 180
  },
  "todos": [
    {
      "id": "jrl-2026-07-14-01",
      "content": "Set up 2 BCG mock case trials with Claude",
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
  ]
}
```

## Field rules

| Field | Rule |
|-------|------|
| `todos[].id` | unique, `jrl-<for_date>-NN` |
| `todos[].content` | task text — **Hermes todo_tool field name** (not `text`) |
| `todos[].status` | always `"pending"` — Hermes enum: pending / in_progress / completed / cancelled |
| `todos[].category` | `bcg maguro perf-coach hermes trip finance health running cooking errands general` |
| `todos[].priority` | `high medium low` — advisory; Hermes todo_tool has no priority field (list order = priority), so Hermes should insert high→top |
| `todos[].confidence` | 0–1; server drops anything < 0.4 before publishing |
| `todos[].due` / `defer_until` | optional ISO dates; omitted unless the journal named one |
| `threads[].sentiment` | `positive worry tension neutral` |
| `threads` | 2–5 arcs, merged day-over-day by `key` (prior day's threads are fed back to the model) |

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
