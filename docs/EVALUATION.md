# Evaluation

This is a product-quality evaluation on one synthetic chat, not a benchmark. It answers two narrow questions:

1. Does the briefing preserve the core material items a careful human would pick?
2. Does every displayed item carry valid evidence and grounded owner/deadline fields?

## Data

- Source: `examples/sample_chat.txt`, a synthetic 111-line college-fest group chat (108 parsed messages, 6 participants).
- Gold set: `evaluation/gold_sample.json`, hand-labelled by the author. It covers **25** core items: 2 decisions, 12 actions, 3 questions and 8 updates, plus the two genuine venue contradictions.
- The gold set is deliberately partial. It does **not** support a precision claim.

## Matching

An item counts as matched when any of its evidence ids appears in the gold row's `ids`. Several rows list alternative messages because the chat repeats or resolves an item across adjacent turns. Recall is reported per kind and overall.

Conflict preservation checks whether at least one displayed item citing either side of a gold conflict carries a `conflict_group`.

## Mechanical checks

`scripts/evaluate.py` also checks:

- every displayed item has at least one id that exists in the parsed transcript;
- every `owner_status = supported` action has an owner stated in its cited text or matching its sender;
- every `deadline_status = supported` action has its deadline phrase in the cited text;
- the reading metric is calculated from the displayed items and their unique cited sources.

## Results

Generated from the committed `examples/sample_briefing.json` with:

```bash
uv run python scripts/evaluate.py
```

| Metric | Result |
| --- | --- |
| Core items matched | **21 / 25 (84%)** |
| Decisions | 2 / 2 (100%) |
| Actions | 9 / 12 (75%) |
| Questions | 3 / 3 (100%) |
| Updates | 7 / 8 (88%) |
| Conflicts preserved | 2 / 2 |
| Displayed items with invalid citation | 0 |
| Supported owners not in evidence | 0 |
| Supported deadlines not in evidence | 0 |
| Displayed / deferred items | 32 / 19 |
| Candidates labelled | 54 / 54 |
| Model calls / duration | 5 / 384.91 s |

Conflict preservation:

| Conflict | Preserved |
| --- | :---: |
| Hall booked vs hall closed | yes |
| Library free vs library closes early | yes |

Reading:

- briefing body: **524 words** vs 949 chat words (**45% shorter**);
- briefing body plus its unique cited source messages: **958 words** (**0.9% longer** than the chat);
- method: briefing words + unique cited message words, divided by 200 wpm; full chat uses all parsed user-message words.

## Interpretation

- **Citation validity is enforced by construction**, not learned. A displayed item always has a parsed source id; unsupported model fields are replaced.
- **The 84% is displayed recall, not extraction recall.** Four gold items were deliberately deferred by the section cap: forwarding the projector request, taking the second shift, holding the schedule, and the final deferral of the venue decision. They remain in the transcript and the deferred count is visible.
- **Actions are the weakest section.** Three secondary assignments were deferred, while the deadline-bearing and decision-blocking actions survived.
- **The reading metric is not a productivity claim.** The body is 45% shorter, but verifying every citation makes the total roughly the same length as the chat. CatchUp Local is a triage and evidence tool, not a compression trick.
- **The model is not being benchmarked.** The evaluation tests the product contract around the model: evidence, grounding, conflict preservation and failure behaviour.
