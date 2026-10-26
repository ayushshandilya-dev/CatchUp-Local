# Architecture

CatchUp Local is deliberately split between deterministic code, a bounded local-model task, and a validation layer. The separation is the product: it is what makes a small model useful for a privacy-sensitive job.

## 1. Parsing (`parser/`)

The parser accepts WhatsApp text exports in the common bracketed and unbracketed forms, including 12-hour and 24-hour timestamps, `DD/MM/YY`, `MM/DD/YY`, dotted and dashed dates.

Design rules:

- **Preserve rather than guess.** Lines that look like timestamps but fail validation become visible `parse_error` messages with a content-free warning.
- **Detect date order over the whole file.** If the export is genuinely ambiguous, it is flagged and an assumption is exposed in the UI and JSON.
- **Keep multiline messages together.** Continuation lines are appended to the previous message and counted.
- **Mark, do not delete.** Media placeholders, deleted messages, edits and system notices are preserved as flags.
- **Stable ids.** `m0001`, `m0002`, … are assigned in source order and reused everywhere.

Wall-clock timestamps have no timezone; the tool does not invent one.

## 2. Candidate retrieval (`analysis/preprocessing.py`)

A deterministic cue lexicon proposes candidates: explicit decisions, tasks/requests/commitments, unresolved questions, and material updates. Social questions and pure greetings are filtered. Instruction-like messages are excluded from candidacy entirely.

Candidate retrieval intentionally favours recall. The model's job is to remove noise and correct kinds, not to discover items from a 111-line transcript in one shot. Earlier experiments with open-ended extraction over large chunks were slow and unreliable on a 1.5B model; this design is the result.

Each candidate carries a **prior kind** derived from cues. That prior is a hint, never evidence.

## 3. Local model (`llm/`)

`OllamaClient` talks to the Ollama HTTP API directly using the standard library. It enforces a loopback host, exposes typed errors (`ollama_unavailable`, `model_unavailable`, `ollama_timeout`, `invalid_json`), and sends a JSON schema for constrained decoding.

The prompt (`llm/prompts.py`):

- frames the transcript as untrusted data;
- defines the five labels: `decision`, `action`, `question`, `update`, `skip`;
- asks for an owner/deadline only on actions, copied from the message;
- requires one item per candidate, in order;
- is paired with a dynamic schema whose `items` array has `minItems = maxItems = batch size`, and whose `id` is an enum of the batch.

The pipeline (`llm/pipeline.py`) sends fixed-size batches (default 12), reports progress, retries malformed output once with a repair prompt, and marks a batch failed rather than inventing labels. Fatal Ollama errors stop the run with a typed message.

### Why not let the model write the summary?

Because a fluent summary is exactly where unsupported claims hide. The overview is derived from validated items by code (`_derived_summary`). The model never writes user-facing prose.

## 4. Validation (`validation/evidence.py`)

Every label is checked before it can appear:

1. **Id validity.** The id must belong to the batch and not be duplicated.
2. **Instruction exclusion.** Instruction-like messages cannot be evidence.
3. **Kind reconciliation.** A strong explicit decision/action wording can override a weaker model label; the correction is recorded in `warnings`.
4. **Owner grounding.** A model owner must appear in the cited message (sender or text). Otherwise a deterministically extracted, grounded owner is used, or `unknown`.
5. **Deadline grounding.** The exact phrase (normalised) must appear in the cited message. Otherwise a stated deadline is used, or `unknown`.
6. **Certainty.** `explicit` only when the wording carries an explicit cue; otherwise `interpretation`.
7. **Merge.** Adjacent/near-duplicate items of the same kind merge when their owners and deadlines are compatible. Evidence ids are unioned; the longer text is kept.
8. **Conflicts.** Items that mention the same venue or facility and take opposite availability positions (for example “free” vs “closes early”, “decided” vs “closed”) get a shared `conflict_group`. Both remain visible. No winner is chosen, and unrelated disagreement words such as “but” do not create a conflict.
9. **Focus cap.** To keep the briefing readable, each section keeps a bounded number of items, preferring conflicts, grounded deadlines/owners and explicit wording. The rest are counted in `items_deferred` and in a visible warning; they remain in the transcript.
10. **Read-first.** Conflicts, supported deadlines, open questions and model `read` flags are ranked, with at most one item per conflict group; each recommendation cites a message.

`refused_claims` summarises rejected labels by reason without exposing chat text.

## 5. API and UI (`web/`)

FastAPI serves a single-page UI and a small JSON API:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Ollama/model status, limits |
| `GET` | `/api/sample` | committed precomputed briefing |
| `POST` | `/api/analyze` | upload and start a job |
| `POST` | `/api/analyze-sample` | live run on the sample |
| `GET` | `/api/jobs/{id}` | progress and result |

Jobs run in a daemon thread and live in process memory only. The store is capped and expired after one hour. Uploads are capped at 8 MB, 100k lines and 20k messages by the parser/config.

The UI is dependency-free HTML/CSS/JS. It uses `textContent` for all chat-derived text, shows evidence in a side drawer, and exposes counts, warnings, refused claims and the original transcript.

## 6. Failure model

| Failure | Behaviour |
| --- | --- |
| Ollama down | typed error; no briefing |
| Model missing | typed error with `ollama pull` hint |
| Timeout | typed error; no briefing |
| Malformed JSON | one repair attempt |
| Some batches invalid | those items show explicit wording only; run marked degraded |
| All batches invalid | typed `analysis_failed`; nothing fabricated |
| Model omits a strong decision/action | item shown from explicit wording with a warning |
| Unsupported owner/deadline | replaced with `unknown` and a warning |

## Trade-offs

- **Speed:** CPU inference is slow. Batching reduces prompt overhead but cannot make a 1.5B model fast on an 8 GB dual-core laptop.
- **Recall vs precision:** deterministic candidate recall plus model triage favours not missing material items; the cost is occasional noise, which is visible and checkable.
- **Model choice:** `qwen2.5:1.5b` was more consistent at instruction-following than `gemma3:1b` in local tests, at similar latency. The model is configurable; the committed sample records the model used.
