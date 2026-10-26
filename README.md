# CatchUp Local

**Turn a WhatsApp export into a briefing you can verify — using a local open-weight model.**

CatchUp Local reads a WhatsApp group export, finds the decisions, action items, open questions and material updates, and links every single item back to the message it came from. The model runs through Ollama on your machine. There is no cloud fallback, no telemetry, and no stored chat by default.

Built for the **DEV Hacktoberfest 2026 Week 3 "Slow and Steady"** challenge.

![CatchUp Local sample briefing](docs/screenshot.png)

## Why another chat summariser?

Most "summarise my WhatsApp" demos produce confident prose and hide their seams. A 1–2B local model will sometimes attribute a task to the wrong person or invent a deadline. CatchUp Local is designed around that constraint:

- **Evidence or nothing.** Every item keeps the id and exact text of its source message. Claims without a valid citation are refused.
- **Grounded fields.** An owner survives only if a participant is stated in the cited message. A deadline survives only if the exact phrase appears there. Otherwise it becomes `unknown`.
- **Conflicts are preserved.** If one message books the hall and another says it is closed, both are shown with a conflict marker. No winner is invented.
- **The model is used where it helps.** Deterministic code parses the export and retrieves candidates. The model only triages those candidates. This hybrid is a reliability decision, documented openly.
- **Honest degradation.** A failed batch is marked as untriaged. The UI shows counts, warnings, refused claims and the exact source transcript.

## Run it

Requirements: macOS/Linux, Python 3.11+, [Ollama](https://ollama.com), and enough RAM for a 1.5B model (tested on an 8 GB Intel Mac).

```bash
ollama pull qwen2.5:1.5b
uv sync --extra dev
uv run catchup-local
```

Open <http://127.0.0.1:8000>. Click **Show the sample briefing** for an instant precomputed result, or **Run the sample live** to watch the local model work through the batches.

To analyse your own export:

1. In WhatsApp: **Chat → More → Export chat → Without media**.
2. Upload the resulting `.txt` file.
3. Keep the tab open while the local model runs. On the 8 GB reference machine, the sample takes roughly 6–10 minutes. That is the "slow" part — and the reason the privacy story is real.

Environment overrides:

```bash
CATCHUP_MODEL=qwen2.5:1.5b CATCHUP_TIMEOUT_SECONDS=300 uv run catchup-local
```

Only loopback Ollama hosts are accepted. A non-loopback `CATCHUP_OLLAMA_HOST` raises at startup.

## The sample

`examples/sample_chat.txt` is a synthetic 111-line group chat about a college tech fest. It includes decisions, assignments, a venue reversal, a budget disagreement, an unanswered registration-desk question, media placeholders, a deleted message, an edit, system messages, and a prompt-injection attempt.

`examples/sample_briefing.json` is the committed output of a full local run, generated with `qwen2.5:1.5b` and prompt version `2026-10-23.3`. It is labelled **Precomputed sample** in the UI. Live runs may differ — that is the point of showing citations.

## How it works

```
WhatsApp .txt
   │
   ├─ deterministic parser ── timestamps, senders, multiline, media,
   │                          edits, deleted messages, unparsed lines
   │
   ├─ candidate retrieval ─── cue lexicons: decisions, actions,
   │                          questions, updates; instruction-like text excluded
   │
   ├─ local model ─────────── fixed-size batches; label each candidate;
   │                          owner and deadline only for actions
   │
   ├─ validation ──────────── citation check, owner/deadline grounding,
   │                          duplicate merge, conflict preservation
   │
   └─ derived briefing ────── summary from validated items, reading first,
                              coverage, refused claims, warnings
```

More detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

### What the model is asked to do

The model receives a compact list of candidate messages (`id | suggested kind | sender | text`) and returns one JSON label per candidate:

```json
{"items":[{"id":"m0012","kind":"action","owner":"Priya","deadline":"Friday 6pm","read":true}]}
```

A dynamic JSON schema fixes the batch cardinality and restricts ids to the batch, so a small model cannot quietly return only the easy half. The model never assigns evidence ids and never writes the final prose.

## Reliability measures

- **Fixed cardinality.** `minItems`/`maxItems` equal the batch size; ids are an enum of the batch.
- **One repair attempt.** Malformed JSON gets one compact repair prompt, then the batch is marked failed.
- **Deterministic fallbacks.** If the model omits a high-confidence decision/action, it is shown from its explicit wording with a warning. If the model's owner/deadline is not grounded, a stated value from the message is used, otherwise `unknown`.
- **No silent fallback.** Ollama unavailable or timeout is a typed error, not a deterministic briefing pretending to be AI.
- **Instruction isolation.** Instruction-like messages are treated as quoted content, never executed, and cannot be the only evidence for an item.
- **No chat content in logs.** Logs contain error codes only.

## Tests

```bash
uv run pytest
```

The suite covers:

- parser: multiline messages, orphan lines, ambiguous date order, media/edit/deleted/system flags;
- candidate retrieval and owner/deadline grounding;
- validation: fabricated owners/deadlines, invalid ids, instruction-only evidence, duplicate merge, venue-conflict preservation, section caps/deferred counts;
- pipeline: complete run, partial model output, invalid output, Ollama unavailable;
- API: health, precomputed sample, upload/job lifecycle, filename sanitisation, invalid uploads.

## Evaluation

See [`docs/EVALUATION.md`](docs/EVALUATION.md) for the gold set, matching rules and measured results. The short version: 21 of 25 core items were displayed, both venue conflicts were preserved, and every supported owner/deadline appears in its cited message. Citation validity and grounding are enforced by construction; label quality is measured against a small hand-labelled sample, not claimed as benchmark performance.

## Privacy

See [`docs/PRIVACY.md`](docs/PRIVACY.md). In short:

- uploads are parsed in memory;
- live briefings live in process memory only;
- the model endpoint must be loopback;
- no analytics, no accounts, no cloud APIs;
- the precomputed sample is the only result stored on disk.

## Project layout

```
src/catchup_local/
  parser/          deterministic WhatsApp parsing
  analysis/        candidate retrieval, grounding, reading metrics
  llm/             loopback Ollama client, prompts, pipeline
  validation/      evidence checks, merge, conflicts
  schemas/         model and public briefing schemas
  web/             FastAPI app and static UI
examples/          synthetic chat and committed sample briefing
evaluation/        hand-labelled gold set for the sample
tests/             parser, analysis, validation, pipeline, API tests
docs/              architecture, privacy, evaluation, DEV article
scripts/           regenerate the precomputed sample
```

## Limitations

- It reads an exported file, not live WhatsApp. Quoted replies and context outside the export are invisible.
- A 1.5B model can still choose the wrong label. Citations make errors checkable; they do not make labels infallible.
- Conservative grounding means some real owners/deadlines will be `unknown` when the message is implicit.
- CPU inference is slow. That is a deliberate trade for local-only processing.
- The sample is synthetic and small. It is a product demo, not a benchmark.

## License

MIT. See [`LICENSE`](LICENSE).
