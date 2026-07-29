"""Reviewer-facing report generation.

Everything in the report is derived from the artifacts at runtime — no case
text, score, or example is hardcoded, so a replacement `cases.json` produces a
correspondingly different report.
"""

from __future__ import annotations

from collections import Counter

from .pipeline import utc_now
from .scoring import PASS_THRESHOLD, REVIEW_THRESHOLD

FLAG_LABELS = {
    "sensitive_info_request": "Response solicits sensitive information",
    "absolute_guarantee": "Absolute / unconditional language",
    "required_points_missing": "Required policy point not covered",
    "disallowed_actions": "Overlap with a disallowed action",
    "dismissive_language": "Dismissive phrasing",
    "no_next_step": "No actionable next step",
    "too_short": "Response too short",
    "prompt_safety": "Evaluator-manipulation attempt in case text",
}


def _truncate(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _md_escape(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def build_report(
    cases: list[dict],
    rule_results: dict[str, dict],
    evaluations: list[dict],
    scores: list[dict],
    disagreement_rows: list[dict],
    run_mode: str,
    provider: str,
    model: str,
    mode_reason: str,
    models_used: list[str] | None = None,
    switch_log: list[dict] | None = None,
) -> str:
    cases_by_id = {c["case_id"]: c for c in cases}
    evals_by_id = {e["case_id"]: e for e in evaluations}
    scores_by_id = {s["case_id"]: s for s in scores}
    is_mock = run_mode != "api"

    lines: list[str] = []
    a = lines.append

    a("# Support Answer Evaluation Report")
    a("")
    a(f"- **Generated:** {utc_now()}")
    a(f"- **Cases evaluated:** {len(scores)}")
    a(f"- **Evaluation mode:** `{run_mode}` (provider `{provider}`, model `{model}`) — {mode_reason}")
    if switch_log:
        mixed = len(models_used or []) > 1
        a("")
        a("> ⚠️ **Model fallback occurred during this run.**")
        for switch in switch_log:
            a(f">   - `{switch['from']}` → `{switch['to']}` — "
              f"{_md_escape(_truncate(switch.get('reason', ''), 200))}")
        a(">")
        if mixed:
            a("> Cases in this run were served by more than one model "
              f"({', '.join(f'`{m}`' for m in models_used)}), so verdicts are **not "
              "strictly comparable across cases**.")
        else:
            a("> Every case that produced a verdict was still served by a single "
              f"model (`{(models_used or ['unknown'])[0]}`), so verdicts remain "
              "comparable — the switch happened before any case was evaluated.")
    if is_mock:
        a("")
        a("> ⚠️ **MOCK RUN.** No LLM was called. The `policy_adherence`, "
          "`customer_helpfulness`, and `risk_level` verdicts below were derived "
          "from the deterministic rule checks by the fallback evaluator, not by a "
          "model. Model-judgment findings in this report are therefore not "
          "independent evidence. Set `ANTHROPIC_API_KEY` and re-run for a real "
          "evaluation.")
    a("")

    # -- distribution -------------------------------------------------------
    status_counts = Counter(s["final_status"] for s in scores)
    avg = round(sum(s["final_score"] for s in scores) / len(scores), 1) if scores else 0.0
    a("## Outcome distribution")
    a("")
    a(f"| pass | review | fail | mean score |")
    a("| ---: | -----: | ---: | ---------: |")
    a(f"| {status_counts.get('pass', 0)} | {status_counts.get('review', 0)} "
      f"| {status_counts.get('fail', 0)} | {avg} |")
    a("")
    a(f"Status bands: `pass` ≥ {PASS_THRESHOLD}, `review` ≥ {REVIEW_THRESHOLD}, "
      f"`fail` below {REVIEW_THRESHOLD}.")
    a("")

    # -- summary table ------------------------------------------------------
    a("## Case summary")
    a("")
    a("| case_id | score | status | policy | helpfulness | risk (LLM) | risk (rules) | rule flags |")
    a("| ------- | ----: | ------ | ------ | ----------- | ---------- | ------------ | ---------- |")
    for score in sorted(scores, key=lambda s: s["final_score"]):
        cid = score["case_id"]
        verdict = score["llm_verdict"]
        flags = ", ".join(score["rule_flags"]) or "—"
        a(
            f"| `{cid}` | {score['final_score']} | **{score['final_status']}** "
            f"| {verdict['policy_adherence']} | {verdict['customer_helpfulness']} "
            f"| {verdict['risk_level']} | {rule_results[cid]['rule_risk_level']} "
            f"| {_md_escape(flags)} |"
        )
    a("")

    # -- failure patterns ---------------------------------------------------
    a("## Top failure patterns")
    a("")
    flag_counter: Counter[str] = Counter()
    for rule_result in rule_results.values():
        flag_counter.update(rule_result["flags"])
    verdict_counter = Counter(
        e["policy_adherence"] for e in evaluations if e["policy_adherence"] != "pass"
    )
    if flag_counter:
        a("| pattern | cases | share |")
        a("| ------- | ----: | ----: |")
        total = len(rule_results) or 1
        for flag, count in flag_counter.most_common():
            label = FLAG_LABELS.get(flag, flag)
            a(f"| {label} (`{flag}`) | {count} | {round(100 * count / total)}% |")
        a("")
    else:
        a("No deterministic failure patterns fired across this case set.")
        a("")
    if verdict_counter:
        summary = ", ".join(f"{v}: {n}" for v, n in verdict_counter.most_common())
        a(f"Non-passing model policy verdicts — {summary}.")
        a("")

    # -- deterministic catches ---------------------------------------------
    a("## Where deterministic checks caught something important")
    a("")
    det_examples = _pick_deterministic_examples(rule_results, scores, limit=2)
    if det_examples:
        for idx, (cid, headline, evidence) in enumerate(det_examples, start=1):
            case = cases_by_id[cid]
            score = scores_by_id[cid]
            a(f"### {idx}. `{cid}` — {headline}")
            a("")
            a(f"> **User:** {_truncate(case['user_message'])}")
            a(f">")
            a(f"> **Assistant:** {_truncate(case['assistant_response'])}")
            a("")
            for line in evidence:
                a(f"- {line}")
            a(f"- Final: **{score['final_score']} / {score['final_status']}** "
              f"(ceiling applied: {score['components']['applied_ceiling']}).")
            a("- Caught in code, with no model call and no per-case tuning: the same "
              "check runs identically on any case file with this schema.")
            a("")
    else:
        a("No deterministic check fired on this case set — every response cleared "
          "the rule-based signals. On a clean input file this section is expected "
          "to be empty; the checks still ran (see `rule_checks.json`).")
        a("")

    # -- LLM value ----------------------------------------------------------
    a("## Where LLM judgment added value beyond keyword checks")
    a("")
    llm_examples = _pick_llm_examples(rule_results, evals_by_id, scores_by_id, limit=2)
    if llm_examples:
        for idx, (cid, headline, evidence) in enumerate(llm_examples, start=1):
            case = cases_by_id[cid]
            a(f"### {idx}. `{cid}` — {headline}")
            a("")
            a(f"> **Assistant:** {_truncate(case['assistant_response'])}")
            a("")
            for line in evidence:
                a(f"- {line}")
            a("")
        if is_mock:
            a("> Note: in mock mode these observations are rule-derived, so this "
              "section demonstrates the mechanism rather than genuine model insight.")
            a("")
    else:
        a("On this case set the model's verdicts tracked the deterministic signals "
          "closely, so no case shows a clear model-only finding. The mechanism "
          "still applies: the model sees the full text and can flag implied "
          "promises, wrong framing, or a required point that is contradicted "
          "rather than merely absent — none of which a keyword check can model.")
        a("")

    # -- disagreements ------------------------------------------------------
    a("## Rule / model disagreements")
    a("")
    if disagreement_rows:
        a("| case_id | rules | model | direction | likely cause |")
        a("| ------- | ----- | ----- | --------- | ------------ |")
        for row in disagreement_rows:
            a(f"| `{row['case_id']}` | {row['rule_risk_level']} | {row['llm_risk_level']} "
              f"| {row['direction']} | {_md_escape(_truncate(row['likely_cause'], 180))} |")
        a("")
        a("Full detail in `disagreements.json`.")
    else:
        a("Deterministic and model risk levels agreed on every case. "
          "`disagreements.json` is present and empty.")
    a("")

    # -- limitations --------------------------------------------------------
    a("## Known limitations, false positives and false negatives")
    a("")
    a("**Deterministic checks**")
    a("")
    a("- *Required-point coverage is lexical.* A response can satisfy a required "
      "point in wording that shares few stems with the policy phrasing and be "
      "scored as missing (**false positive**). Conversely, echoing the policy's "
      "words while contradicting them scores as covered (**false negative**) — "
      "this is exactly the gap the LLM stage is there to close.")
    a("- *The synonym map is hand-built.* It covers the vocabulary of account, "
      "payment and data-retention support. A case file from a different domain "
      "will match less well and coverage false positives will rise.")
    a("- *Absolute-language detection is context-free.* \"We never ask for your "
      "password\" contains `never` and is penalised as soft absolute language "
      "even though it is good practice. The hedging-relief term only partly "
      "offsets this.")
    a("- *Sensitive-information detection requires co-occurrence* of a sensitive "
      "noun and a soliciting verb in one sentence, with a negation guard. A "
      "solicitation split across two sentences (\"I'll need one more thing. "
      "Your PIN.\") is missed (**false negative**); an unusual phrasing of a "
      "secret not in the lexicon is missed entirely.")
    a("- *Next-step detection is pattern-based.* An unusual but valid next step "
      "outside the pattern list reads as \"no next step\" (**false positive**).")
    a("- *`approx_tokens` is characters ÷ 4*, a rough proxy. It is reported only, "
      "never used in scoring.")
    a("")
    a("**Model judgment**")
    a("")
    a("- The model sees the rule-check output. That is deliberate (it can "
      "contradict them), but it is also an anchoring risk: a wrong deterministic "
      "flag may bias the verdict toward agreement, which would also suppress the "
      "disagreement analysis.")
    a("- Verdicts are not guaranteed stable across runs. Nothing here averages "
      "multiple samples, so a borderline case can move between `warning` and "
      "`pass` between runs and cross a status boundary.")
    a("- The prompt forbids inventing policy, but the model can still over-read "
      "the supplied `policy_context` — treating an allowed action as mandatory, "
      "for instance.")
    a("")
    a("**Scoring**")
    a("")
    a("- Weights and penalty constants are chosen judgement calls, not calibrated "
      "against labelled data. They encode a deliberate bias toward flagging: on "
      "this design a borderline case lands in `review`, not `pass`.")
    a("- Ceilings mean two very different responses can land on the same score "
      "(both capped). Read `explanation` in `final_scores.json`, not the number "
      "alone.")
    a("- Rule and model penalties partly overlap (both react to a missing "
      "required point), so a genuine defect is penalised twice by design. This "
      "makes the pipeline conservative, and it is the main reason scores skew low.")
    a("")

    a("## Artifacts")
    a("")
    a("| file | contents |")
    a("| ---- | -------- |")
    a("| `cases.json` | pipeline input |")
    a("| `rule_checks.json` | deterministic signals per case |")
    a("| `llm_evaluations.json` | one structured model verdict per case |")
    a("| `final_scores.json` | code-computed score, status and explanation |")
    a("| `report.md` | this document |")
    a("| `llm_calls.jsonl` | one record per LLM call attempt |")
    a("| `disagreements.json` | rule/model risk divergences |")
    a("| `run_manifest.json` | per-stage status and timings |")
    a("")

    return "\n".join(lines) + "\n"


def _pick_deterministic_examples(rule_results, scores, limit=2):
    """Rank cases by the severity of what the rule checks caught."""
    severity = {
        "sensitive_info_request": 100,
        "disallowed_actions": 70,
        "required_points_missing": 55,
        "absolute_guarantee": 40,
        "prompt_safety": 35,
        "dismissive_language": 25,
        "no_next_step": 20,
        "too_short": 10,
    }
    scores_by_id = {s["case_id"]: s for s in scores}
    ranked = []
    for cid, rule_result in rule_results.items():
        flags = rule_result["flags"]
        if not flags:
            continue
        ranked.append((max(severity.get(f, 0) for f in flags), cid, rule_result))
    ranked.sort(key=lambda row: (-row[0], scores_by_id[row[1]]["final_score"], row[1]))

    out = []
    for _, cid, rule_result in ranked[:limit]:
        checks = rule_result["checks"]
        evidence = []
        headline = "deterministic signals fired"
        if checks["sensitive_info_request"]["flagged"]:
            headline = "credential solicitation caught without a model call"
            terms = ", ".join(checks["sensitive_info_request"]["sensitive_terms_mentioned"])
            evidence.append(
                f"`sensitive_info_request` fired: sensitive term(s) `{terms}` appear in the "
                "same sentence as a soliciting verb, with no negation."
            )
        if checks["disallowed_actions"]["matched_actions"]:
            headline = "overlap with an explicitly disallowed action"
            evidence.append(
                "`disallowed_actions` matched: "
                + "; ".join(f"`{a}`" for a in checks["disallowed_actions"]["matched_actions"])
            )
        if checks["required_points"]["missing_points"]:
            if headline == "deterministic signals fired":
                headline = "required policy guidance missing"
            evidence.append(
                f"`required_points` coverage {checks['required_points']['covered_count']}"
                f"/{checks['required_points']['total_count']}; not evidenced: "
                + "; ".join(f"`{p}`" for p in checks["required_points"]["missing_points"])
            )
        guarantee = checks["absolute_guarantee"]
        if guarantee["flagged"]:
            if headline == "deterministic signals fired":
                headline = "unconditional promise detected"
            terms = ", ".join(guarantee["strong_terms"] + guarantee["soft_terms"])
            evidence.append(
                f"`absolute_guarantee` ({guarantee['severity']} severity) on: `{terms}`."
            )
        if checks["dismissive_language"]["flagged"]:
            evidence.append("`dismissive_language` fired on brush-off phrasing.")
        if not checks["next_step"]["offered"]:
            evidence.append("`next_step` found no actionable follow-up for the customer.")
        if checks["prompt_safety"]["flagged"]:
            evidence.append(
                "`prompt_safety` flagged evaluator-manipulation markers in the case text."
            )
        out.append((cid, headline, evidence))
    return out


def _pick_llm_examples(rule_results, evals_by_id, scores_by_id, limit=2):
    """Find cases where the model contributed something the rules did not."""
    risk_order = {"low": 0, "medium": 1, "high": 2}
    ranked = []
    for cid, rule_result in rule_results.items():
        evaluation = evals_by_id[cid]
        rule_flags = set(rule_result["flags"])
        gain = 0
        notes = []

        if risk_order[evaluation["risk_level"]] > risk_order[rule_result["rule_risk_level"]]:
            gain += 3
            notes.append(
                f"Model rated risk `{evaluation['risk_level']}` where the deterministic "
                f"bands only reached `{rule_result['rule_risk_level']}`."
            )
        violations = evaluation.get("policy_violations") or []
        lexical_hits = set(rule_result["checks"]["disallowed_actions"]["matched_actions"])
        if violations and not lexical_hits:
            gain += 3
            notes.append(
                f"Model cited {len(violations)} policy violation(s) with no lexical overlap "
                "against the disallowed-action list — a semantic read, not a keyword match: "
                + "; ".join(f"“{v}”" for v in violations[:2])
            )
        if evaluation["policy_adherence"] != "pass" and "sensitive_info_request" not in rule_flags:
            gain += 2
            notes.append(
                f"Model judged policy adherence `{evaluation['policy_adherence']}` without a "
                "hard deterministic breach signal."
            )
        if evaluation["customer_helpfulness"] != "pass" and rule_result["checks"]["next_step"]["offered"]:
            gain += 2
            notes.append(
                f"Model judged helpfulness `{evaluation['customer_helpfulness']}` even though "
                "the next-step pattern matched — it read the response as not actually "
                "moving the customer forward."
            )
        reasoning = evaluation.get("reasoning") or []
        if reasoning:
            notes.append("Model reasoning: " + "; ".join(_truncate(r, 120) for r in reasoning[:2]))
        fix = evaluation.get("recommended_fix")
        if fix:
            gain += 1
            notes.append(f"Actionable rewrite the rules cannot produce: “{_truncate(fix, 180)}”")

        if gain >= 3:
            ranked.append((gain, cid, notes))

    ranked.sort(key=lambda row: (-row[0], scores_by_id[row[1]]["final_score"], row[1]))
    out = []
    for gain, cid, notes in ranked[:limit]:
        evaluation = evals_by_id[cid]
        headline = (
            f"model risk `{evaluation['risk_level']}` / adherence "
            f"`{evaluation['policy_adherence']}` beyond the keyword signals"
        )
        out.append((cid, headline, notes))
    return out
