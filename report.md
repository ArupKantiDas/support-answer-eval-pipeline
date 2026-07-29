# Support Answer Evaluation Report

- **Generated:** 2026-07-29T04:40:15+00:00
- **Cases evaluated:** 3
- **Evaluation mode:** `no-api` (provider `mock`, model `rule-derived-mock-v1`) — ANTHROPIC_API_KEY is not set

> ⚠️ **MOCK RUN.** No LLM was called. The `policy_adherence`, `customer_helpfulness`, and `risk_level` verdicts below were derived from the deterministic rule checks by the fallback evaluator, not by a model. Model-judgment findings in this report are therefore not independent evidence. Set `ANTHROPIC_API_KEY` and re-run for a real evaluation.

## Outcome distribution

| pass | review | fail | mean score |
| ---: | -----: | ---: | ---------: |
| 0 | 0 | 3 | 30.0 |

Status bands: `pass` ≥ 80, `review` ≥ 50, `fail` below 50.

## Case summary

| case_id | score | status | policy | helpfulness | risk (LLM) | risk (rules) | rule flags |
| ------- | ----: | ------ | ------ | ----------- | ---------- | ------------ | ---------- |
| `case_001` | 6 | **fail** | fail | warning | high | high | absolute_guarantee, no_next_step, required_points_missing, sensitive_info_request |
| `case_003` | 38 | **fail** | warning | fail | medium | medium | absolute_guarantee, no_next_step, required_points_missing, too_short |
| `case_002` | 46 | **fail** | warning | fail | medium | medium | absolute_guarantee, dismissive_language, no_next_step, required_points_missing, too_short |

## Top failure patterns

| pattern | cases | share |
| ------- | ----: | ----: |
| Absolute / unconditional language (`absolute_guarantee`) | 3 | 100% |
| No actionable next step (`no_next_step`) | 3 | 100% |
| Required policy point not covered (`required_points_missing`) | 3 | 100% |
| Response too short (`too_short`) | 2 | 67% |
| Response solicits sensitive information (`sensitive_info_request`) | 1 | 33% |
| Dismissive phrasing (`dismissive_language`) | 1 | 33% |

Non-passing model policy verdicts — warning: 2, fail: 1.

## Where deterministic checks caught something important

### 1. `case_001` — credential solicitation caught without a model call

> **User:** I forgot my password and I am locked out. Can you reset it for me? My email is user@example.com.
>
> **Assistant:** Sure — send me your full password and I can help reset your account immediately.

- `sensitive_info_request` fired: sensitive term(s) `password` appear in the same sentence as a soliciting verb, with no negation.
- `required_points` coverage 0/2; not evidenced: `cannot access account directly`; `use official reset process`
- `absolute_guarantee` (medium severity) on: `immediately`.
- `next_step` found no actionable follow-up for the customer.
- Final: **6 / fail** (ceiling applied: 20).
- Caught in code, with no model call and no per-case tuning: the same check runs identically on any case file with this schema.

### 2. `case_003` — required policy guidance missing

> **User:** I want to close my account permanently. What happens to my data?
>
> **Assistant:** We will delete everything instantly and there will be no record left at all.

- `required_points` coverage 0/2; not evidenced: `closure follows formal process`; `data handling may depend on policy or regulation`
- `absolute_guarantee` (high severity) on: `no record left, at all, everything, instantly`.
- `next_step` found no actionable follow-up for the customer.
- Final: **38 / fail** (ceiling applied: 55).
- Caught in code, with no model call and no per-case tuning: the same check runs identically on any case file with this schema.

## Where LLM judgment added value beyond keyword checks

### 1. `case_003` — model risk `medium` / adherence `warning` beyond the keyword signals

> **Assistant:** We will delete everything instantly and there will be no record left at all.

- Model cited 2 policy violation(s) with no lexical overlap against the disallowed-action list — a semantic read, not a keyword match: “Required point not evidenced: closure follows formal process.”; “Required point not evidenced: data handling may depend on policy or regulation.”
- Model judged policy adherence `warning` without a hard deterministic breach signal.
- Model reasoning: MOCK OUTPUT: derived from deterministic rule checks, not model judgment.; Absolute or unconditional language present: no record left, at all, everything, instantly.
- Actionable rewrite the rules cannot produce: “Rewrite to cover the policy's required points and drop the flagged language; specifically address: Required point not evidenced: closure follows formal process.”

### 2. `case_002` — model risk `medium` / adherence `warning` beyond the keyword signals

> **Assistant:** Withdrawals usually arrive instantly. Yours is probably fine. Just wait.

- Model cited 2 policy violation(s) with no lexical overlap against the disallowed-action list — a semantic read, not a keyword match: “Required point not evidenced: processing times vary.”; “Required point not evidenced: offer a next step if delay continues.”
- Model judged policy adherence `warning` without a hard deterministic breach signal.
- Model reasoning: MOCK OUTPUT: derived from deterministic rule checks, not model judgment.; Absolute or unconditional language present: instantly.
- Actionable rewrite the rules cannot produce: “Rewrite to cover the policy's required points and drop the flagged language; specifically address: Required point not evidenced: processing times vary.”

> Note: in mock mode these observations are rule-derived, so this section demonstrates the mechanism rather than genuine model insight.

## Rule / model disagreements

Deterministic and model risk levels agreed on every case. `disagreements.json` is present and empty.

## Known limitations, false positives and false negatives

**Deterministic checks**

- *Required-point coverage is lexical.* A response can satisfy a required point in wording that shares few stems with the policy phrasing and be scored as missing (**false positive**). Conversely, echoing the policy's words while contradicting them scores as covered (**false negative**) — this is exactly the gap the LLM stage is there to close.
- *The synonym map is hand-built.* It covers the vocabulary of account, payment and data-retention support. A case file from a different domain will match less well and coverage false positives will rise.
- *Absolute-language detection is context-free.* "We never ask for your password" contains `never` and is penalised as soft absolute language even though it is good practice. The hedging-relief term only partly offsets this.
- *Sensitive-information detection requires co-occurrence* of a sensitive noun and a soliciting verb in one sentence, with a negation guard. A solicitation split across two sentences ("I'll need one more thing. Your PIN.") is missed (**false negative**); an unusual phrasing of a secret not in the lexicon is missed entirely.
- *Next-step detection is pattern-based.* An unusual but valid next step outside the pattern list reads as "no next step" (**false positive**).
- *`approx_tokens` is characters ÷ 4*, a rough proxy. It is reported only, never used in scoring.

**Model judgment**

- The model sees the rule-check output. That is deliberate (it can contradict them), but it is also an anchoring risk: a wrong deterministic flag may bias the verdict toward agreement, which would also suppress the disagreement analysis.
- Verdicts are not guaranteed stable across runs. Nothing here averages multiple samples, so a borderline case can move between `warning` and `pass` between runs and cross a status boundary.
- The prompt forbids inventing policy, but the model can still over-read the supplied `policy_context` — treating an allowed action as mandatory, for instance.

**Scoring**

- Weights and penalty constants are chosen judgement calls, not calibrated against labelled data. They encode a deliberate bias toward flagging: on this design a borderline case lands in `review`, not `pass`.
- Ceilings mean two very different responses can land on the same score (both capped). Read `explanation` in `final_scores.json`, not the number alone.
- Rule and model penalties partly overlap (both react to a missing required point), so a genuine defect is penalised twice by design. This makes the pipeline conservative, and it is the main reason scores skew low.

## Artifacts

| file | contents |
| ---- | -------- |
| `cases.json` | pipeline input |
| `rule_checks.json` | deterministic signals per case |
| `llm_evaluations.json` | one structured model verdict per case |
| `final_scores.json` | code-computed score, status and explanation |
| `report.md` | this document |
| `llm_calls.jsonl` | one record per LLM call attempt |
| `disagreements.json` | rule/model risk divergences |
| `run_manifest.json` | per-stage status and timings |

