"""Measure the assistant's accuracy on the 25-question evaluation set.

Run the pipeline first. Then:
    python scripts/run_eval.py                      # provider from env: Claude / OpenAI if a key is set, else mock
    python scripts/run_eval.py --provider mock      # offline baseline (no key, used in CI)
    python scripts/run_eval.py --provider anthropic # needs ANTHROPIC_API_KEY

Writes eval/results_<provider>.json and refreshes docs/eval_results.md and the accuracy table in the README
(between the EVAL markers), keeping the results of the other providers.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assistant.core import DataAssistant  # noqa: E402
from src.assistant.knowledge import load_knowledge  # noqa: E402
from src.assistant.llm import resolve_llm  # noqa: E402
from src.assistant.retriever import KnowledgeRetriever  # noqa: E402
from src.config import PipelineConfig  # noqa: E402
from src.evaluation import accuracy, evaluate, load_questions  # noqa: E402

RESULTS_DIR = ROOT / "eval"
README = ROOT / "README.md"
REPORT = ROOT / "docs" / "eval_results.md"
START, END = "<!-- EVAL:START -->", "<!-- EVAL:END -->"
LABELS = {"mock": "Offline baseline: no LLM, reuses the closest worked example",
          "anthropic": "Claude", "openai": "OpenAI"}


def load_all_results() -> list[dict]:
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RESULTS_DIR.glob("results_*.json"))]
    return sorted(runs, key=lambda r: (r["provider"] == "mock", -r["accuracy"]))


def summary_table(runs: list[dict]) -> str:
    lines = ["| Model | Correct | Execution accuracy | Run on |", "|---|---|---|---|"]
    for r in runs:
        label = LABELS.get(r["provider"], r["provider"])
        model = "" if r["provider"] == "mock" else f" (`{r['model']}`)"
        lines.append(f"| {label}{model} | {r['correct']} / {r['total']} | **{r['accuracy']:.0%}** | {r['date']} |")
    return "\n".join(lines)


def write_report(runs: list[dict]) -> None:
    out = ["# Evaluation results", "",
           "Each question in [`eval/questions.yaml`](../eval/questions.yaml) has a gold SQL query. The assistant's "
           "answer counts as correct when its result matches the gold result (execution accuracy; see "
           "[`src/evaluation.py`](../src/evaluation.py)). Regenerate with `python scripts/run_eval.py`.", "",
           summary_table(runs), ""]
    for r in runs:
        out += [f"## {LABELS.get(r['provider'], r['provider'])} · `{r['model']}`", "",
                "| # | Question | Result | Detail |", "|---|---|---|---|"]
        for x in r["results"]:
            out.append(f"| {x['id']} | {x['question']} | {'✅' if x['correct'] else '❌'} | "
                       f"{'' if x['correct'] else x['reason'].replace('|', '/')} |")
        out.append("")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(out), encoding="utf-8")


def update_readme(runs: list[dict]) -> None:
    if not README.exists():
        return
    text = README.read_text(encoding="utf-8")
    if START in text and END in text:
        head, rest = text.split(START, 1)
        _, tail = rest.split(END, 1)
        README.write_text(f"{head}{START}\n{summary_table(runs)}\n{END}{tail}", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", choices=["anthropic", "openai", "mock"], default=None)
    parser.add_argument("--limit", type=int, default=None, help="only the first N questions (quick check)")
    parser.add_argument("--min-accuracy", type=float, default=None, help="exit with an error below this accuracy")
    args = parser.parse_args()

    knowledge = load_knowledge()
    retriever = KnowledgeRetriever(knowledge)
    llm = resolve_llm(retriever, args.provider)
    db = PipelineConfig().db_path
    assistant = DataAssistant(db, llm, knowledge, retriever, explain=False)
    questions = load_questions()[: args.limit]
    print(f"Evaluating {llm.provider}:{llm.model} on {len(questions)} questions")

    def progress(r):
        print(f"  {r.id} {'OK  ' if r.correct else 'MISS'} {r.question[:70]:<70} {'' if r.correct else r.reason}")

    results = evaluate(assistant, db, questions, progress)
    acc = accuracy(results)
    print(f"Execution accuracy: {acc:.0%} ({sum(r.correct for r in results)}/{len(results)})")

    if args.limit is None:
        run = {"provider": llm.provider, "model": llm.model, "date": date.today().isoformat(), "accuracy": acc,
               "correct": sum(r.correct for r in results), "total": len(results),
               "results": [asdict(r) for r in results]}
        (RESULTS_DIR / f"results_{llm.provider}.json").write_text(json.dumps(run, indent=2, ensure_ascii=False),
                                                                    encoding="utf-8")
        runs = load_all_results()
        write_report(runs)
        update_readme(runs)
    if args.min_accuracy is not None and acc < args.min_accuracy:
        sys.exit(f"Accuracy {acc:.0%} is below the minimum {args.min_accuracy:.0%}")


if __name__ == "__main__":
    main()
