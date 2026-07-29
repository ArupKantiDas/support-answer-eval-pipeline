#!/usr/bin/env python3
"""Independent artifact validation: python validate.py

Runs against the artifacts on disk only. It needs no API key, no network, and
no knowledge of how the pipeline ran — if the files are wrong it says so.

Checks
------
1. Every required artifact exists and is non-empty.
2. Every JSON artifact parses, and the structured ones match their schema.
3. Exactly one LLM evaluation exists per case, ids line up both ways.
4. Final scores are reproducible from rule_checks + llm_evaluations by
   re-running the code-side scoring function (proves scores are computed in
   code, not authored by the model).
5. The report was generated after the stages it depends on (manifest order and
   timestamps), and the report file itself is present and substantive.
6. Every logged LLM call has the required fields and corresponds to a produced
   evaluation; every evaluation has a successful call record.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from evalpipe.pipeline import MANIFEST_FILE, STAGES
from evalpipe.schema import CASE_SCHEMA, LLM_EVALUATION_SCHEMA, SchemaError, validate
from evalpipe.scoring import PASS_THRESHOLD, REVIEW_THRESHOLD, score_case

CASES_FILE = "cases.json"
RULE_CHECKS_FILE = "rule_checks.json"
LLM_EVALS_FILE = "llm_evaluations.json"
FINAL_SCORES_FILE = "final_scores.json"
REPORT_FILE = "report.md"
CALL_LOG_FILE = "llm_calls.jsonl"
DISAGREEMENTS_FILE = "disagreements.json"

# cases.json may live outside the artifact directory (`--cases other.json
# --outdir run2`), so it is resolved separately from the generated artifacts.
REQUIRED_ARTIFACTS = [
    RULE_CHECKS_FILE,
    LLM_EVALS_FILE,
    FINAL_SCORES_FILE,
    REPORT_FILE,
    CALL_LOG_FILE,
]
OPTIONAL_ARTIFACTS = [DISAGREEMENTS_FILE, MANIFEST_FILE]

CALL_LOG_REQUIRED_FIELDS = [
    "stage",
    "case_id",
    "timestamp",
    "provider",
    "model",
    "prompt_hash",
    "input_artifacts",
    "output_artifact",
]


class Validator:
    def __init__(self, directory: Path, cases_path: Path | None = None) -> None:
        self.dir = directory
        self.cases_path = self._resolve_cases_path(cases_path)
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.passed: list[str] = []

    def _resolve_cases_path(self, explicit: Path | None) -> Path:
        """Explicit flag wins, then the manifest's recorded input, then default."""
        if explicit:
            return Path(explicit).resolve()
        manifest = self.dir / MANIFEST_FILE
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                for record in data.get("stages", []):
                    recorded = (record.get("details") or {}).get("cases_file")
                    if record.get("stage") == "INIT" and recorded:
                        return Path(recorded)
            except (json.JSONDecodeError, OSError):
                pass
        return self.dir / CASES_FILE

    # -- reporting helpers -------------------------------------------------
    def ok(self, message: str) -> None:
        self.passed.append(message)

    def fail(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def _load_json(self, name: str):
        path = self.cases_path if name == CASES_FILE else self.dir / name
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.fail(f"{name}: missing")
        except json.JSONDecodeError as exc:
            self.fail(f"{name}: invalid JSON ({exc})")
        return None

    # -- checks ------------------------------------------------------------
    def check_artifacts_exist(self) -> None:
        if not self.cases_path.is_file():
            self.fail(f"input case file missing: {self.cases_path}")
        elif self.cases_path.stat().st_size == 0:
            self.fail(f"input case file is empty: {self.cases_path}")
        else:
            self.ok(f"input case file present: {self.cases_path.name} "
                    f"({self.cases_path.stat().st_size} bytes)")
        for name in REQUIRED_ARTIFACTS:
            path = self.dir / name
            if not path.exists():
                self.fail(f"required artifact missing: {name}")
            elif path.stat().st_size == 0:
                self.fail(f"required artifact is empty: {name}")
            else:
                self.ok(f"artifact present: {name} ({path.stat().st_size} bytes)")
        for name in OPTIONAL_ARTIFACTS:
            path = self.dir / name
            if path.exists():
                self.ok(f"optional artifact present: {name}")
            else:
                self.warn(f"optional artifact missing: {name}")

    def run(self) -> bool:
        self.check_artifacts_exist()

        cases = self._load_json(CASES_FILE)
        rule_checks = self._load_json(RULE_CHECKS_FILE)
        evaluations = self._load_json(LLM_EVALS_FILE)
        scores = self._load_json(FINAL_SCORES_FILE)
        if any(x is None for x in (cases, rule_checks, evaluations, scores)):
            return self.summarise()
        self.ok("all JSON artifacts parse")

        # -- case validity --------------------------------------------------
        for i, case in enumerate(cases):
            try:
                validate(case, CASE_SCHEMA, f"cases[{i}]")
            except SchemaError as exc:
                self.fail(f"{CASES_FILE}: {exc}")
        case_ids = [c["case_id"] for c in cases]
        if len(set(case_ids)) != len(case_ids):
            self.fail(f"{CASES_FILE}: duplicate case_id values")
        else:
            self.ok(f"{len(case_ids)} case(s) with unique ids and a valid schema")

        # -- one evaluation per case ---------------------------------------
        for evaluation in evaluations:
            try:
                validate(evaluation, LLM_EVALUATION_SCHEMA, f"{LLM_EVALS_FILE}[{evaluation.get('case_id')}]")
            except SchemaError as exc:
                # `mock`/`provider`/`model` are pipeline annotations; strip before schema check
                stripped = {k: v for k, v in evaluation.items()
                            if k not in ("mock", "provider", "model")}
                try:
                    validate(stripped, LLM_EVALUATION_SCHEMA, f"{LLM_EVALS_FILE}[{evaluation.get('case_id')}]")
                except SchemaError:
                    self.fail(f"{LLM_EVALS_FILE}: {exc}")

        eval_ids = [e["case_id"] for e in evaluations]
        missing_evals = sorted(set(case_ids) - set(eval_ids))
        extra_evals = sorted(set(eval_ids) - set(case_ids))
        dupe_evals = sorted({i for i in eval_ids if eval_ids.count(i) > 1})
        if missing_evals:
            self.fail(f"{LLM_EVALS_FILE}: no evaluation for case(s) {missing_evals}")
        if extra_evals:
            self.fail(f"{LLM_EVALS_FILE}: evaluation for unknown case(s) {extra_evals}")
        if dupe_evals:
            self.fail(f"{LLM_EVALS_FILE}: duplicate evaluation(s) for {dupe_evals}")
        if not (missing_evals or extra_evals or dupe_evals):
            self.ok(f"exactly one LLM evaluation per case ({len(eval_ids)}/{len(case_ids)})")

        if any(e.get("mock") for e in evaluations):
            self.warn(
                "evaluations contain mock=true records — these are rule-derived "
                "fallback outputs, not model judgments"
            )

        # -- rule checks per case -------------------------------------------
        rule_ids = [r["case_id"] for r in rule_checks]
        if sorted(rule_ids) != sorted(case_ids):
            self.fail(f"{RULE_CHECKS_FILE}: case coverage mismatch (got {sorted(rule_ids)})")
        else:
            self.ok(f"rule checks cover all {len(rule_ids)} case(s)")
        for rule_result in rule_checks:
            for required in ("checks", "flags", "rule_risk_level"):
                if required not in rule_result:
                    self.fail(f"{RULE_CHECKS_FILE}[{rule_result.get('case_id')}]: missing '{required}'")

        # -- scores recomputed in code --------------------------------------
        self.check_scores_in_code(rule_checks, evaluations, scores, case_ids)

        # -- report ordering -------------------------------------------------
        self.check_report_ordering()

        # -- call log --------------------------------------------------------
        self.check_call_log(case_ids, eval_ids)

        # -- disagreements ---------------------------------------------------
        self.check_disagreements(case_ids)

        return self.summarise()

    def check_scores_in_code(self, rule_checks, evaluations, scores, case_ids) -> None:
        score_ids = [s["case_id"] for s in scores]
        if sorted(score_ids) != sorted(case_ids):
            self.fail(f"{FINAL_SCORES_FILE}: case coverage mismatch")
            return

        rules_by_id = {r["case_id"]: r for r in rule_checks}
        evals_by_id = {e["case_id"]: e for e in evaluations}
        mismatches = []
        for score in scores:
            cid = score["case_id"]
            if not isinstance(score.get("final_score"), int):
                self.fail(f"{FINAL_SCORES_FILE}[{cid}]: final_score must be an integer")
                continue
            if not 0 <= score["final_score"] <= 100:
                self.fail(f"{FINAL_SCORES_FILE}[{cid}]: final_score out of range 0-100")
            if score.get("final_status") not in ("pass", "review", "fail"):
                self.fail(f"{FINAL_SCORES_FILE}[{cid}]: final_status must be pass|review|fail")
            if not score.get("explanation"):
                self.fail(f"{FINAL_SCORES_FILE}[{cid}]: missing explanation")

            # Status must follow from the score, in code.
            expected_status = (
                "pass" if score["final_score"] >= PASS_THRESHOLD
                else "review" if score["final_score"] >= REVIEW_THRESHOLD
                else "fail"
            )
            if score.get("final_status") != expected_status:
                self.fail(
                    f"{FINAL_SCORES_FILE}[{cid}]: status {score.get('final_status')!r} "
                    f"does not follow from score {score['final_score']} "
                    f"(expected {expected_status!r})"
                )

            # Recompute from source artifacts — the real proof of code scoring.
            recomputed = score_case(rules_by_id[cid], evals_by_id[cid])
            if recomputed["final_score"] != score["final_score"] or \
               recomputed["final_status"] != score["final_status"]:
                mismatches.append(
                    f"{cid}: stored {score['final_score']}/{score['final_status']} "
                    f"!= recomputed {recomputed['final_score']}/{recomputed['final_status']}"
                )

        if mismatches:
            self.fail(
                "final scores are not reproducible from rule_checks + llm_evaluations "
                "via scoring.score_case: " + "; ".join(mismatches)
            )
        else:
            self.ok(
                "final scores reproduce exactly when recomputed in code from "
                f"{RULE_CHECKS_FILE} + {LLM_EVALS_FILE} ({len(scores)} case(s))"
            )

        for evaluation in evaluations:
            for key in ("final_score", "score", "final_status", "status"):
                if key in evaluation:
                    self.fail(
                        f"{LLM_EVALS_FILE}[{evaluation['case_id']}]: contains '{key}' — "
                        "the LLM must not produce final scores or statuses"
                    )

    def check_report_ordering(self) -> None:
        report_path = self.dir / REPORT_FILE
        if not report_path.exists():
            return
        text = report_path.read_text(encoding="utf-8")
        for heading in ("Case summary", "Top failure patterns", "limitations"):
            if heading.lower() not in text.lower():
                self.warn(f"{REPORT_FILE}: expected a '{heading}' section")

        manifest = self._load_json(MANIFEST_FILE) if (self.dir / MANIFEST_FILE).exists() else None
        if manifest is None:
            self.warn(f"{MANIFEST_FILE} absent — falling back to file mtimes for stage ordering")
            report_mtime = report_path.stat().st_mtime
            for dependency in (RULE_CHECKS_FILE, LLM_EVALS_FILE, FINAL_SCORES_FILE):
                dep_path = self.dir / dependency
                if dep_path.exists() and dep_path.stat().st_mtime > report_mtime + 1:
                    self.fail(f"{REPORT_FILE} is older than {dependency}")
            self.ok("report is at least as new as its input artifacts (mtime check)")
            return

        completed = manifest.get("completed_stages", [])
        if completed != STAGES[: len(completed)]:
            self.fail(f"{MANIFEST_FILE}: stages completed out of order -> {completed}")
            return

        index = {rec["stage"]: rec for rec in manifest.get("stages", [])}
        report_rec = index.get("REPORT_GENERATED")
        if report_rec is None:
            self.fail(f"{MANIFEST_FILE}: REPORT_GENERATED stage never completed")
            return
        report_started = _parse_ts(report_rec.get("started_at"))
        for dependency in ("RULE_CHECKS_COMPLETE", "LLM_EVAL_COMPLETE", "SCORES_AGGREGATED"):
            rec = index.get(dependency)
            if rec is None:
                self.fail(f"{MANIFEST_FILE}: {dependency} never completed, but the report exists")
                continue
            done = _parse_ts(rec.get("completed_at"))
            if done and report_started and done > report_started:
                self.fail(
                    f"{MANIFEST_FILE}: {dependency} completed after REPORT_GENERATED started"
                )
        self.ok("report generated after rule checks, LLM evaluation and score aggregation")

    def check_call_log(self, case_ids, eval_ids) -> None:
        path = self.dir / CALL_LOG_FILE
        if not path.exists():
            return
        records = []
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                self.fail(f"{CALL_LOG_FILE}:{lineno}: invalid JSON ({exc})")
        if not records:
            self.fail(f"{CALL_LOG_FILE}: no call records")
            return

        for i, record in enumerate(records):
            missing = [f for f in CALL_LOG_REQUIRED_FIELDS if f not in record]
            if missing:
                self.fail(f"{CALL_LOG_FILE}[{i}]: missing field(s) {missing}")
            if not isinstance(record.get("input_artifacts"), list):
                self.fail(f"{CALL_LOG_FILE}[{i}]: input_artifacts must be a list")
            if _parse_ts(record.get("timestamp")) is None:
                self.fail(f"{CALL_LOG_FILE}[{i}]: timestamp is not ISO-8601 ({record.get('timestamp')!r})")

        logged_ids = {r.get("case_id") for r in records}
        unknown = sorted(logged_ids - set(case_ids))
        if unknown:
            self.fail(f"{CALL_LOG_FILE}: call(s) logged for unknown case(s) {unknown}")

        # Each produced evaluation needs a successful call record.
        successes: dict[str, int] = {}
        for record in records:
            if record.get("status", "ok") == "ok":
                successes[record["case_id"]] = successes.get(record["case_id"], 0) + 1
        for cid in eval_ids:
            count = successes.get(cid, 0)
            if count == 0:
                self.fail(f"{CALL_LOG_FILE}: evaluation for {cid} has no successful call record")
            elif count > 1:
                self.fail(f"{CALL_LOG_FILE}: {count} successful call records for {cid} (expected 1)")

        for record in records:
            if record.get("output_artifact") != LLM_EVALS_FILE:
                self.fail(
                    f"{CALL_LOG_FILE}: record for {record.get('case_id')} points at "
                    f"{record.get('output_artifact')!r}, expected {LLM_EVALS_FILE!r}"
                )
                break

        retries = len(records) - sum(successes.values())
        if retries:
            self.warn(f"{CALL_LOG_FILE}: {retries} failed attempt(s) were retried")

        # Fallback detection: more than one model across successful calls means
        # cases were not all judged by the same evaluator.
        serving_models: list[str] = []
        for record in records:
            if record.get("status", "ok") == "ok":
                name = record.get("model")
                if name and name not in serving_models:
                    serving_models.append(name)
        if len(serving_models) > 1:
            self.warn(
                f"{CALL_LOG_FILE}: model fallback occurred — cases were served by "
                f"{' then '.join(serving_models)}; verdicts are not strictly "
                "comparable across cases"
            )
        elif serving_models:
            self.ok(f"all cases served by a single model: {serving_models[0]}")
        self.ok(
            f"{len(records)} logged call(s) match the produced evaluations "
            f"({len(successes)} case(s), one success each)"
        )

    def check_disagreements(self, case_ids) -> None:
        path = self.dir / DISAGREEMENTS_FILE
        if not path.exists():
            return
        rows = self._load_json(DISAGREEMENTS_FILE)
        if rows is None:
            return
        if not isinstance(rows, list):
            self.fail(f"{DISAGREEMENTS_FILE}: expected a JSON array")
            return
        for row in rows:
            if row.get("case_id") not in case_ids:
                self.fail(f"{DISAGREEMENTS_FILE}: unknown case_id {row.get('case_id')!r}")
            for field in ("rule_risk_level", "llm_risk_level", "likely_cause"):
                if field not in row:
                    self.fail(f"{DISAGREEMENTS_FILE}[{row.get('case_id')}]: missing '{field}'")
        self.ok(f"disagreements file valid ({len(rows)} row(s))")

    def summarise(self) -> bool:
        print("=" * 68)
        print("VALIDATION")
        print("=" * 68)
        for message in self.passed:
            print(f"  PASS  {message}")
        for message in self.warnings:
            print(f"  WARN  {message}")
        for message in self.errors:
            print(f"  FAIL  {message}")
        print("-" * 68)
        print(f"{len(self.passed)} passed, {len(self.warnings)} warning(s), {len(self.errors)} error(s)")
        print("=" * 68)
        return not self.errors


def _parse_ts(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate pipeline artifacts.")
    parser.add_argument("--dir", default=".", help="directory containing the artifacts")
    parser.add_argument("--cases", default=None,
                        help="input case file (default: read from run_manifest.json, "
                             "else <dir>/cases.json)")
    args = parser.parse_args(argv)
    cases = Path(args.cases).resolve() if args.cases else None
    ok = Validator(Path(args.dir).resolve(), cases).run()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
