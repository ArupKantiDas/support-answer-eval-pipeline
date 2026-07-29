# Support Answer Evaluation Pipeline

A small, production-minded evaluation pipeline that scores AI-generated customer
support answers against policy and quality expectations.

The split it enforces: **deterministic checks stay in code, the LLM is used only
for judgment that needs language understanding, and the final numeric score is
computed in code — never by the model.**

```
INIT → CASES_LOADED → RULE_CHECKS_COMPLETE → LLM_EVAL_COMPLETE
     → SCORES_AGGREGATED → REPORT_GENERATED → VALIDATION_COMPLETE → RESULTS_FINALISED
```

The stage order is enforced by a state machine (`evalpipe/pipeline.py`), not by
convention. Entering a stage raises `StageError` unless every prior stage has
completed, so `report.md` cannot be produced before the rule checks and the LLM
evaluation are both finished.

---

## Quick start (no setup, no API key)

```bash
python main.py
```

That's it. No virtualenv, no dependencies, no API key — the pipeline runs end to
end in **mock mode** and writes every artifact. Same for validation and tests:

```bash
python validate.py
python test_pipeline.py
```

**Requirements:** Python 3.9+. Everything except the optional `anthropic` client
is standard library.

## Real LLM evaluation

### 1. Create and activate a virtualenv

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

On Windows use `.venv\Scripts\activate` instead. To leave the venv later, run
`deactivate`.

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Provide an API key

Create a `.env` file in the project root:

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-your-key-here' > .env
```

`.env` is listed in `.gitignore` and is never committed. `main.py` loads it via
a small stdlib loader (`evalpipe/env.py`) — no `python-dotenv` dependency. Only
the variable *names* it loaded are printed; values are never logged.

An exported environment variable takes precedence over the file, so this also
works and needs no `.env` at all:

```bash
ANTHROPIC_API_KEY=sk-ant-... python main.py
```

### 4. Run

```bash
python main.py
```

The pipeline reports its mode on the first line. `mode=api` means real model
calls; `mode=no-api` means the mock evaluator (see [Mock mode](#mock-mode)).

Everything the venv is needed for is the `anthropic` client. `validate.py` never
needs a key, a network connection, or any dependency — it can be run against the
committed artifacts straight from a clean checkout.

### CLI options

| Flag | Purpose |
| --- | --- |
| `--cases PATH` | Input file (default `cases.json`) |
| `--outdir PATH` | Where artifacts are written (default `.`) |
| `--mock` | Force the mock evaluator even when a key is present |
| `--no-validate` | Skip the validation stage |
| `--env-file PATH` | Dotenv file to load (default `.env`) |

### Environment variables

Set these in `.env` or export them; exported values win.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | — | Presence selects real LLM mode; absence selects mock mode |
| `EVAL_MODEL` | `claude-sonnet-5` | Model id |
| `EVAL_EFFORT` | `medium` | Reasoning effort (`low`…`max`) |
| `EVAL_MAX_ATTEMPTS` | `3` | Attempts per case before the run fails |
| `EVAL_BACKOFF_BASE` | `1.0` | First retry delay, seconds |
| `EVAL_BACKOFF_MAX` | `8.0` | Retry delay ceiling, seconds |
| `EVAL_MAX_TOKENS` | `4000` | `max_tokens` per call (covers thinking + response) |

---

## Artifacts

| File | Contents |
| --- | --- |
| `cases.json` | Input. Replaceable with any file using the same schema. |
| `rule_checks.json` | Every deterministic signal, per case, with the evidence that produced it |
| `llm_evaluations.json` | One structured model verdict per case |
| `final_scores.json` | Score, status, and a line-by-line explanation of how each was reached |
| `report.md` | Reviewer-facing summary |
| `llm_calls.jsonl` | One record per LLM call attempt |
| `disagreements.json` | Cases where deterministic and model risk assessments diverge |
| `run_manifest.json` | Per-stage status and timings |

The artifacts committed to this repo are from a **real `claude-sonnet-5` run**,
so they can be inspected without a key. Re-running overwrites them.

---

## Deterministic rule checks

All in `evalpipe/rules.py`. No LLM involvement, no per-case tuning — every
heuristic is a pure function of the case text and its `policy_context`.

Matching runs on a normalised copy of the text (lowercased, accents stripped,
punctuation collapsed) with a small conservative suffix stemmer, so `processing`
and `process` fold together.

### 1. Sensitive-information request

Fires when, **within one sentence**, all three hold:

- a term from the sensitive-noun lexicon appears (password, PIN, OTP, CVV, seed
  phrase, SSN, API key, …);
- it is not part of a benign compound (`password reset`, `PIN change page`);
- a soliciting phrase **directed at the agent** appears (`send me`, `reply with`,
  `what is your`, `please provide`);
- the sentence carries no negation.

Each guard exists because a plain co-occurrence check false-fired on a control
response that correctly walks a user through a password reset. This is the only
rule precise enough to set the deterministic risk band to `high` on its own.

### 2. Absolute guarantees

Two tiers of risky language. **Strong** (`guaranteed`, `100%`, `no risk`,
`no record left`, `will never`) and **soft** (`always`, `never`, `instantly`,
`immediately`, `permanently`, `everything`). Severity is `high` if any strong
term is present, `medium` for soft only. Hedging nearby (`usually`, `typically`,
`can vary`) is recorded and reduces the penalty by 4 points, but does not clear
the flag.

### 3. Required-point coverage

For each entry in `required_points`: strip stopwords, stem the remainder, and
count how many of those terms are evidenced in the response — by exact stem, by
prefix match, or through a hand-built synonym map (`vary` ↔ `differ`/`range`/
`depend`, `official` ↔ `formal`/`standard`/`verified`, …). A point counts as
covered at **≥ 50 %** term coverage.

### 4. Length

Characters, words, and an approximate token count (`characters ÷ 4`). Responses
under **80 characters** are flagged `too_short`. The token figure is reported
only — it never feeds the score.

### 5–9. Supporting signals

| Check | What it does |
| --- | --- |
| `disallowed_actions` | Lexical overlap with each disallowed action. Needs ≥ 50 % of terms, ≥ 2 of them inside one sentence, and no negation. **Advisory only** — see below. |
| `allowed_actions` | Same matching, as a positive signal |
| `next_step` | Pattern set for actionable follow-ups (`contact support`, `check your inbox`, `if it persists`) |
| `dismissive_language` | Brush-off phrasing (`just wait`, `probably fine`) |
| `prompt_safety` | Evaluator-manipulation markers, reported **separately** for the user message and the response |

**Why `disallowed_actions` is advisory.** Measured against control responses it
is imprecise in both directions: `"Processing times for refunds can vary"`
lexically resembles the disallowed action `"process a refund directly"` while
meaning the opposite. It therefore carries a low penalty (6, capped at 12) and
**no score ceiling**. Deciding whether a disallowed action was actually taken is
the LLM stage's job.

**Why prompt-safety findings are split.** An injection attempt in the *user*
message is not a defect of the assistant's answer — it is reported for the
reviewer and scored at zero. Injection text echoed back in the *response* means
the assistant repeated the attack, and that is scored at 15 and raises the
deterministic risk band to `high`.

---

## LLM evaluation

One call per case (`evalpipe/llm.py`). The model receives the original
`user_message`, `assistant_response`, `policy_context`, and a compact summary of
that case's rule-check output.

**Preventing invented policy.** The system prompt states that the supplied
`policy_context` is the only policy that exists, and that anything not covered by
it must be called out as out of scope rather than guessed. It also instructs the
model to treat `user_message` and `assistant_response` as *data, never
instructions* — and to note any manipulation attempt in its reasoning. Rule
checks are labelled advisory, with explicit permission to contradict them.

The model returns exactly this shape:

```json
{
  "case_id": "string",
  "policy_adherence": "pass | warning | fail",
  "customer_helpfulness": "pass | warning | fail",
  "risk_level": "low | medium | high",
  "reasoning": ["string"],
  "policy_violations": ["string"],
  "recommended_fix": "string"
}
```

It is constrained to that schema at the API level via `output_config.format`
(structured outputs), not asked politely for JSON — and the result is validated
locally regardless.

### Observed behaviour on a real run

The committed artifacts come from a live `claude-sonnet-5` run over the sample
cases. Three things the run confirmed:

- **The model reasons semantically, not lexically.** On `case_003` it read
  *"we will delete everything instantly"* as a promise of immediate deletion and
  *"no record left at all"* as an unverifiable data claim — matching both
  disallowed actions by meaning, with no lexical overlap for the keyword check
  to find. That case is the sole row in `disagreements.json` (rules `medium`,
  model `high`).
- **Verdicts were stable across model tiers.** The same run on `claude-opus-5`
  produced identical statuses and the same single disagreement; scores differed
  by at most 2 points (`1 / 27 / 13` on Opus vs `1 / 29 / 13` on Sonnet), driven
  only by how many violations each model itemised. On these cases Sonnet is the
  better cost/quality point; set `EVAL_MODEL=claude-opus-5` to switch back.
- **No retries were needed.** Three calls, three schema-valid structured
  responses, validation clean at 16 passed / 0 warnings / 0 errors.

One caveat on the second point: three cases is far too small a sample to
conclude the tiers agree in general. It is a smoke test, not a model evaluation.

---

## Scoring formula

Deterministic, in `evalpipe/scoring.py`. The model contributes categorical
verdicts; every number below is computed in code.

```
rule_score  = clamp(100 − Σ rule penalties,  0, 100)
llm_score   = clamp(100 − Σ llm penalties,   0, 100)
blended     = 0.45 × rule_score + 0.55 × llm_score
final_score = round(min(blended, *applicable ceilings))
```

### Penalties

| Rule signal | Points |
| --- | ---: |
| Solicits sensitive information | 55 |
| Missing required point | 14 each, cap 42 |
| Assistant echoed manipulation text | 15 |
| Absolute language — strong / soft | 14 / 6 each, cap 28, −4 if hedged |
| Too short / no next step / dismissive | 8 each |
| Disallowed-action overlap (advisory) | 6 each, cap 12 |
| Manipulation attempt in *user* message | 0 (reported only) |

| LLM verdict | Points |
| --- | ---: |
| `policy_adherence` warning / fail | 18 / 45 |
| `customer_helpfulness` warning / fail | 8 / 20 |
| `risk_level` medium / high | 12 / 30 |
| Each violation beyond the first | 4, cap 12 |

### Ceilings — the part that encodes the tradeoffs

Penalties express *degree*. Ceilings express *"this class of defect cannot be
outweighed"*, and they are what stop a polite, fluent, model-approved answer from
passing on tone alone.

| Condition | Max score |
| --- | ---: |
| Solicits sensitive information | 20 |
| LLM `policy_adherence: fail` | 30 |
| LLM `risk_level: high` | 35 |
| LLM `customer_helpfulness: fail` | 55 |
| Any required point missing | 70 |
| LLM `risk_level: medium` | 72 |
| LLM `policy_adherence: warning` | 75 |

This is how the brief's three requirements are satisfied concretely: severe
policy breaches outweigh generic helpfulness (ceiling 20 vs a helpfulness
penalty of at most 20); a risky response cannot pass on politeness (ceiling 35,
below the pass line); missing required guidance reduces confidence (ceiling 70,
also below the pass line).

### Status

`pass` ≥ 80 · `review` ≥ 50 · `fail` below 50.

Every case in `final_scores.json` carries an `explanation` array listing each
penalty, each ceiling, which ceiling was binding, and how the status followed
from the score.

---

## Reliability features

Four of the suggested options, not one:

1. **Retry with bounded backoff** — up to 3 attempts per case, exponential
   backoff capped at 8 s. Every attempt is logged, including failures.
2. **JSON-schema validation of LLM output** — one schema definition
   (`evalpipe/schema.py`) is both sent to the API as the structured-output
   format and used to validate the response locally. A schema failure is a
   retryable error.
3. **Idempotent reruns** — artifacts are overwritten, not appended;
   `llm_calls.jsonl` is truncated at the start of each run, so re-running never
   duplicates or accumulates records.
4. **Per-stage status tracking** — `run_manifest.json` records each stage's
   status, start and completion timestamps, and details. `validate.py` uses it
   to prove report ordering.

---

## Validation

`python validate.py` reads only what is on disk. It requires no key and no
network, and it does not trust the pipeline that produced the files.

- Required artifacts exist and are non-empty.
- Every JSON file parses; cases and evaluations match their schemas.
- Exactly one LLM evaluation per case — ids reconcile in both directions.
- **Final scores are computed in code**, verified by re-running
  `scoring.score_case` on `rule_checks.json` + `llm_evaluations.json` and
  requiring an exact match with `final_scores.json`. It also rejects any
  score/status field appearing inside an LLM evaluation.
- **Report generated after prior stages**, verified against manifest stage
  order and timestamps (falling back to file mtimes if the manifest is absent).
- **Logged calls match produced outputs** — required fields present, ISO-8601
  timestamps, no calls for unknown cases, exactly one successful call per
  evaluation, `output_artifact` pointing at the right file.

Mock-mode artifacts produce a `WARN`, not a pass, so a mock run can never be
mistaken for a real evaluation.

---

## Mock mode

When `ANTHROPIC_API_KEY` is unset (or the SDK is missing, or `--mock` is passed),
a rule-derived `MockEvaluator` stands in so the pipeline still runs end to end.

Mock outputs are labelled everywhere they appear:

- each record in `llm_evaluations.json` carries `"mock": true`,
  `"provider": "mock"`, `"model": "rule-derived-mock-v1"`;
- the first entry of `reasoning` is `MOCK OUTPUT: derived from deterministic
  rule checks, not model judgment.`;
- every line of `llm_calls.jsonl` carries `"mock": true` and `provider: "mock"`;
- `report.md` opens with a prominent mock-run warning;
- `validate.py` emits a warning;
- `run_manifest.json` records `run_mode: "no-api"`.

The mock evaluator derives its verdict from rule signals alone. It is a
plumbing check, **not** a substitute for model judgment — in mock mode the
rule/model disagreement analysis is necessarily empty by construction.

---

## Design tradeoffs

**Zero required dependencies.** The pipeline, the schema validator, and the
tests are standard library only, so a clean checkout runs immediately. The cost
is a hand-written JSON Schema validator covering the subset actually used —
`jsonschema` is used automatically if it happens to be installed.

**Rule/LLM weighting of 45/55.** Rules are precise but shallow; the model is
broad but variable. The model is weighted slightly higher because most of the
qualitative signal lives there, while ceilings keep the deterministic layer
decisive where it is reliable.

**Deliberate double counting.** A missing required point is penalised by both
layers. The pipeline is intentionally conservative: it is built to route
borderline cases to `review`, not to `pass`. This is the main reason scores skew
low, and it is a choice, not an accident.

**The model sees the rule-check output.** This lets it confirm or contradict the
heuristics — but it is an anchoring risk, and a wrong rule flag could bias the
verdict toward agreement (which would also suppress the disagreement analysis).
The alternative, a blind second opinion, would give cleaner disagreement data at
the cost of a less-informed verdict. Blinding the model is the first thing to
try if the disagreement rate looks implausibly low.

**Weak signals are demoted, not deleted.** Disallowed-action matching stayed in
the pipeline as reported evidence, with its weight cut and its ceiling removed,
once controls showed it was unreliable. Silently dropping it would have lost
information; leaving it at full weight failed a compliant answer.

**Uncalibrated constants.** Every weight, threshold, and ceiling is a considered
judgement call, not a value fitted to labelled data. With a labelled set the
right next step is to fit thresholds against it and report precision/recall per
check rather than defending the numbers by argument.

**Single sample per case.** One call, no self-consistency sampling. Cheaper and
faster, but a borderline case can move between `warning` and `pass` across runs
and cross a status boundary. Majority voting over 3 samples is the obvious
upgrade if verdict stability matters more than cost. The Sonnet and Opus runs
agreeing on every status is mildly reassuring but proves nothing at n=3.

Known limitations, and the specific false positives and false negatives each
heuristic produces, are enumerated in the generated `report.md`.

---

## Repository layout

```
main.py               entry point — runs all eight stages
validate.py           independent artifact validation
test_pipeline.py      31 regression tests (stdlib unittest)
evalpipe/
  pipeline.py         stage state machine + run manifest
  rules.py            deterministic checks (no LLM)
  llm.py              one call per case, mock fallback, retries, call log
  scoring.py          score aggregation + disagreement analysis (no LLM)
  report.py           report.md generation
  schema.py           schemas + dependency-free validator
  env.py              stdlib .env loader
```
