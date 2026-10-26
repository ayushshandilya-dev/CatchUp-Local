from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from catchup_local.config import SAMPLE_CHAT, load_settings
from catchup_local.llm.ollama import OllamaClient
from catchup_local.llm.pipeline import BriefingPipeline
from catchup_local.llm.prompts import PROMPT_VERSION
from catchup_local.parser.parser import parse_export


def main() -> int:
    settings = load_settings()
    parsed = parse_export(SAMPLE_CHAT.read_bytes(), source_name=SAMPLE_CHAT.name)
    pipeline = BriefingPipeline(OllamaClient(settings), settings)
    last = {"phase": "", "done": 0, "total": 0}

    def progress(phase: str, done: int, total: int) -> None:
        if (phase, done, total) != (last["phase"], last["done"], last["total"]):
            print(f"[{time.strftime('%H:%M:%S')}] {phase} {done}/{total}", flush=True)
        last.update(phase=phase, done=done, total=total)

    result = pipeline.analyze(parsed, progress=progress)
    result["meta"] = {
        "kind": "precomputed_sample",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": settings.model,
        "prompt_version": PROMPT_VERSION,
        "note": "Generated offline from examples/sample_chat.txt. Live runs may differ.",
    }
    output = SAMPLE_CHAT.parent / "sample_briefing.json"
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    b = result["briefing"]
    print("wrote", output)
    print("coverage", json.dumps(b["coverage"], indent=2))
    print("items", {k: len(b[k]) for k in ("updates", "decisions", "actions", "questions", "read_first")})
    print("warnings", json.dumps(b["warnings"], indent=2))
    print("conflicts", [(i["id"], i["conflict_group"]) for k in ("decisions", "actions", "updates", "questions") for i in b[k] if i["conflict_group"]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
