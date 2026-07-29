#!/usr/bin/env python3
"""Entry point: python main.py

Runs the staged evaluation pipeline end to end and writes every artifact.
Works with zero third-party dependencies in mock mode; install `anthropic` and
set ANTHROPIC_API_KEY for real LLM evaluation.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from evalpipe import llm as llm_mod
from evalpipe import rules as rules_mod
from evalpipe import scoring as scoring_mod
from evalpipe.env import load_dotenv
from evalpipe.pipeline import Pipeline, StageError, utc_now
from evalpipe.report import build_report
from evalpipe.schema import CASE_SCHEMA, SchemaError, validate

CASES_FILE = "cases.json"
RULE_CHECKS_FILE = "rule_checks.json"
LLM_EVALS_FILE = "llm_evaluations.json"
FINAL_SCORES_FILE = "final_scores.json"
REPORT_FILE = "report.md"
DISAGREEMENTS_FILE = "disagreements.json"


def write_json(path: Path, payload) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def load_cases(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"input file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise SystemExit(f"{path} must contain a JSON array of cases")
    if not data:
        raise SystemExit(f"{path} contains no cases")

    seen: set[str] = set()
    for i, case in enumerate(data):
        try:
            validate(case, CASE_SCHEMA, f"cases[{i}]")
        except SchemaError as exc:
            raise SystemExit(f"invalid case at index {i}: {exc}") from exc
        cid = case["case_id"]
        if cid in seen:
            raise SystemExit(f"duplicate case_id in {path}: {cid}")
        seen.add(cid)
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the support-answer evaluation pipeline.")
    parser.add_argument("--cases", default=CASES_FILE, help="input case file (default: cases.json)")
    parser.add_argument("--outdir", default=".", help="artifact output directory (default: .)")
    parser.add_argument("--mock", action="store_true", help="force the mock evaluator")
    parser.add_argument("--no-validate", action="store_true", help="skip the validation stage")
    parser.add_argument("--env-file", default=".env", help="dotenv file to load (default: .env)")
    args = parser.parse_args(argv)

    outdir = Path(args.outdir).resolve()
    cases_path = Path(args.cases).resolve()

    # Load .env before selecting the evaluator. Only variable NAMES are ever
    # printed — values are never logged.
    loaded = load_dotenv(args.env_file)
    if loaded:
        print(f"[{utc_now()}] loaded {len(loaded)} variable(s) from {args.env_file}: "
              f"{', '.join(sorted(loaded))}")

    evaluator, run_mode, mode_reason = llm_mod.select_evaluator(force_mock=args.mock)
    pipe = Pipeline(outdir, run_mode)
    pipe.complete("INIT", cases_file=str(cases_path), evaluator_mode=run_mode, reason=mode_reason)

    print(f"[{utc_now()}] mode={run_mode} provider={evaluator.provider} "
          f"model={evaluator.model} ({mode_reason})")
    if run_mode != "api":
        print("  ! Mock mode: outputs are labelled mock=true and are NOT model judgments.")

    # -- CASES_LOADED -------------------------------------------------------
    pipe.enter("CASES_LOADED")
    cases = load_cases(cases_path)
    pipe.complete("CASES_LOADED", case_count=len(cases),
                  case_ids=[c["case_id"] for c in cases])
    print(f"[{utc_now()}] loaded {len(cases)} case(s)")

    # -- RULE_CHECKS_COMPLETE ----------------------------------------------
    pipe.enter("RULE_CHECKS_COMPLETE")
    rule_list = [rules_mod.run_rule_checks(case) for case in cases]
    rule_results = {r["case_id"]: r for r in rule_list}
    write_json(outdir / RULE_CHECKS_FILE, rule_list)
    pipe.complete("RULE_CHECKS_COMPLETE", artifact=RULE_CHECKS_FILE,
                  flagged_cases=sum(1 for r in rule_list if r["flags"]))
    print(f"[{utc_now()}] rule checks -> {RULE_CHECKS_FILE}")

    # -- LLM_EVAL_COMPLETE --------------------------------------------------
    pipe.enter("LLM_EVAL_COMPLETE")
    evaluations = llm_mod.evaluate_cases(
        cases=cases,
        rule_results=rule_results,
        evaluator=evaluator,
        outdir=outdir,
        input_artifacts=[CASES_FILE, RULE_CHECKS_FILE],
        output_artifact=LLM_EVALS_FILE,
    )
    write_json(outdir / LLM_EVALS_FILE, evaluations)
    pipe.complete("LLM_EVAL_COMPLETE", artifact=LLM_EVALS_FILE,
                  call_log=llm_mod.LOG_FILE, evaluations=len(evaluations),
                  provider=evaluator.provider, model=evaluator.model, mock=run_mode != "api")
    print(f"[{utc_now()}] llm evaluations -> {LLM_EVALS_FILE} "
          f"({len(evaluations)} call(s) logged in {llm_mod.LOG_FILE})")

    # -- SCORES_AGGREGATED --------------------------------------------------
    pipe.enter("SCORES_AGGREGATED")
    scores = scoring_mod.aggregate(rule_results, evaluations)
    write_json(outdir / FINAL_SCORES_FILE, scores)
    disagreement_rows = scoring_mod.disagreements(rule_results, evaluations)
    write_json(outdir / DISAGREEMENTS_FILE, disagreement_rows)
    pipe.complete("SCORES_AGGREGATED", artifact=FINAL_SCORES_FILE,
                  disagreements_artifact=DISAGREEMENTS_FILE,
                  computed_in="code (scoring.py)",
                  disagreement_count=len(disagreement_rows))
    print(f"[{utc_now()}] scores -> {FINAL_SCORES_FILE}; "
          f"disagreements -> {DISAGREEMENTS_FILE} ({len(disagreement_rows)})")

    # -- REPORT_GENERATED ---------------------------------------------------
    pipe.enter("REPORT_GENERATED")
    # Belt and braces: the state machine already guarantees this ordering.
    for prerequisite in ("RULE_CHECKS_COMPLETE", "LLM_EVAL_COMPLETE", "SCORES_AGGREGATED"):
        if not pipe.has_completed(prerequisite):
            raise StageError(f"refusing to build report: {prerequisite} not complete")
    report = build_report(
        cases=cases,
        rule_results=rule_results,
        evaluations=evaluations,
        scores=scores,
        disagreement_rows=disagreement_rows,
        run_mode=run_mode,
        provider=evaluator.provider,
        model=evaluator.model,
        mode_reason=mode_reason,
    )
    (outdir / REPORT_FILE).write_text(report, encoding="utf-8")
    pipe.complete("REPORT_GENERATED", artifact=REPORT_FILE)
    print(f"[{utc_now()}] report -> {REPORT_FILE}")

    # -- VALIDATION_COMPLETE ------------------------------------------------
    pipe.enter("VALIDATION_COMPLETE")
    if args.no_validate:
        pipe.complete("VALIDATION_COMPLETE", skipped=True)
        print(f"[{utc_now()}] validation skipped (--no-validate)")
    else:
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "validate.py"), "--dir", str(outdir)],
            capture_output=True,
            text=True,
        )
        sys.stdout.write(proc.stdout)
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr)
            pipe.complete("VALIDATION_COMPLETE", passed=False, exit_code=proc.returncode)
            print(f"[{utc_now()}] VALIDATION FAILED — artifacts written but not finalised")
            return proc.returncode
        pipe.complete("VALIDATION_COMPLETE", passed=True)

    # -- RESULTS_FINALISED --------------------------------------------------
    pipe.run("RESULTS_FINALISED", artifacts=[
        CASES_FILE, RULE_CHECKS_FILE, LLM_EVALS_FILE, FINAL_SCORES_FILE,
        REPORT_FILE, llm_mod.LOG_FILE, DISAGREEMENTS_FILE, pipe.manifest_path.name,
    ])

    print()
    print(f"{'case_id':<12} {'score':>5}  status")
    for score in scores:
        print(f"{score['case_id']:<12} {score['final_score']:>5}  {score['final_status']}")
    print()
    print(f"[{utc_now()}] RESULTS_FINALISED — {len(scores)} case(s) evaluated in {run_mode} mode")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
