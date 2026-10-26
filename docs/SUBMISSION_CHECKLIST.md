# Submission checklist

## Product

- [x] WhatsApp export parser with multiline, media, edit, delete and system flags
- [x] Ambiguous date-order detection and visible assumption
- [x] Local-only Ollama client with loopback enforcement and typed failures
- [x] Candidate retrieval, model triage, evidence validation, conflict preservation
- [x] Derived summary, read-first list, coverage and refused-claim reporting
- [x] FastAPI API with in-memory jobs and progress polling
- [x] Polished responsive UI with evidence drawer and source flags
- [x] Precomputed sample for an instant demo
- [x] Live sample rerun with honest timing warning
- [x] MIT license

## Verification

```bash
uv sync --extra dev
uv run pytest
uv run python scripts/evaluate.py
uv run catchup-local
```

Manual checks:

- [x] `GET /api/health` reports the installed local model
- [x] Precomputed sample renders without Ollama doing any work
- [x] Upload validation rejects wrong extensions and empty files
- [x] Job progress moves through parsing → triage → validation → done
- [x] Evidence drawer opens the exact source message and flags
- [x] Download JSON contains the briefing, parse metadata, transcript and reading metrics
- [x] No chat text appears in server logs
- [x] Non-loopback `CATCHUP_OLLAMA_HOST` raises at startup
- [x] `CATCHUP_MODEL` override is documented

## Docs

- [x] `README.md` with quickstart, architecture summary, tests, privacy and limits
- [x] `docs/ARCHITECTURE.md` with the hybrid design and failure model
- [x] `docs/PRIVACY.md` with data flow and threat model
- [x] `docs/EVALUATION.md` with gold set and measured results
- [x] `docs/DEV_ARTICLE.md` draft
- [x] `docs/screenshot.png`
- [x] `docs/SUBMISSION_CHECKLIST.md` (this file)

## Before publishing

- [ ] Replace `<repository-url>` in the article and README if the repo changes
- [x] Confirm the sample briefing was regenerated after the final code changes
- [ ] Run the full test suite on a clean `uv sync`
- [ ] Confirm no chat export, `.env`, model weights or secrets are tracked
- [ ] Create the public repository and push (explicit authorization required)
- [ ] Publish the DEV article as a draft first and review it in the DEV editor
- [ ] Add the DEV post to the Hacktoberfest submission with tags `#devchallenge` and `#hf26challenge`
- [ ] Check the Week 3 deadline: **25 October 2026, 11:59 PM PDT**

## Suggested DEV metadata

- Title: **I built a local AI that refuses to summarise your WhatsApp without receipts**
- Tags: `devchallenge`, `hf26challenge`, `ai`, `privacy`
- Cover image: `docs/screenshot.png` or a cropped result screenshot
- Canonical repo: the public GitHub URL
- AI disclosure: *some AI* (the article and code were agent-assisted; the design and review are the author's)
