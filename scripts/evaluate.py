"""Evaluate a briefing against the small hand-labelled sample set.

Reports recall per kind and conflict preservation, plus mechanical checks that
every displayed item has valid evidence and grounded fields. It deliberately
does not report precision: the gold set covers core items, not every possible
material message.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "evaluation" / "gold_sample.json"
DEFAULT_BRIEFING = ROOT / "examples" / "sample_briefing.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def main() -> int:
    briefing_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BRIEFING
    result = load(briefing_path)
    gold = load(GOLD)
    briefing = result["briefing"]
    messages = {message["message_id"]: message for message in result["messages"]}

    print(f"# Evaluation: {briefing_path.name}\n")
    coverage = briefing.get("coverage", {})
    print(f"- Model: `{coverage.get('model', 'unknown')}`")
    print(f"- Messages analysed: {coverage.get('messages_analysed')} / {coverage.get('messages_total')}")
    print(f"- Candidates: {coverage.get('candidates_labelled')} labelled, {coverage.get('candidates_skipped')} skipped")
    print(f"- Model calls: {coverage.get('model_calls')}, duration: {coverage.get('duration_seconds')}s")
    print(f"- Instruction-like messages isolated: {coverage.get('instruction_like_messages')}")
    print()

    print("## Recall on core gold items\n")
    print("| Kind | Matched | Gold | Recall |")
    print("| --- | ---: | ---: | ---: |")
    total_matched = 0
    total_gold = 0
    for kind in ("decisions", "actions", "questions", "updates"):
        items = briefing.get(kind, [])
        cited = {message_id for item in items for message_id in item.get("evidence_ids", [])}
        matched = sum(1 for row in gold[kind] if cited & set(row["ids"]))
        total = len(gold[kind])
        total_matched += matched
        total_gold += total
        print(f"| {kind} | {matched} | {total} | {matched / total:.0%} |")
    print(f"| **overall** | **{total_matched}** | **{total_gold}** | **{total_matched / total_gold:.0%}** |")
    print()

    conflict_rows = []
    for row in gold["conflicts"]:
        hits = []
        for item in (
            briefing.get("decisions", [])
            + briefing.get("actions", [])
            + briefing.get("questions", [])
            + briefing.get("updates", [])
        ):
            if set(item.get("evidence_ids", [])) & set(row["ids"]):
                hits.append(item)
        groups = {item.get("conflict_group") for item in hits}
        preserved = bool(hits) and any(group for group in groups)
        conflict_rows.append((row["name"], preserved))
    print("## Conflict preservation\n")
    print("| Conflict | Preserved |")
    print("| --- | :---: |")
    for name, preserved in conflict_rows:
        print(f"| {name} | {'yes' if preserved else 'no'} |")
    print()

    all_items = [
        (kind, item)
        for kind in ("decisions", "actions", "questions", "updates")
        for item in briefing.get(kind, [])
    ]
    bad_evidence = 0
    bad_owner = 0
    bad_deadline = 0
    for kind, item in all_items:
        evidence = [messages.get(message_id) for message_id in item.get("evidence_ids", [])]
        if not evidence or any(message is None for message in evidence):
            bad_evidence += 1
            continue
        if kind == "action":
            owner = (item.get("owner") or "").casefold()
            if owner and owner != "unknown" and item.get("owner_status") == "supported":
                if not any(
                    owner == (message.get("sender") or "").casefold()
                    or owner in (message.get("text") or "").casefold()
                    for message in evidence
                ):
                    bad_owner += 1
            deadline = (item.get("deadline") or "").casefold()
            if deadline and deadline != "unknown" and item.get("deadline_status") == "supported":
                if not any(deadline in (message.get("text") or "").casefold() for message in evidence):
                    bad_deadline += 1

    print("## Mechanical checks\n")
    print(f"- Displayed items: {len(all_items)}")
    print(f"- Items with a missing or invalid citation: {bad_evidence}")
    print(f"- Supported owners not found in evidence: {bad_owner}")
    print(f"- Supported deadlines not found in evidence: {bad_deadline}")
    reading = result.get("reading", {})
    print(f"- Reading change: {reading.get('reduction_percent')}% ({reading.get('briefing_words')} briefing words + {reading.get('cited_message_words')} cited source words vs {reading.get('total_words')} chat words)")
    print(f"- Refused claims: {sum(row['count'] for row in briefing.get('refused_claims', []))}")
    print()
    print("Recall uses alternative ids for duplicates. Precision is not claimed: the gold set is partial.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
