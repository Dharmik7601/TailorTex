# Feature: Feedback Loop

## Files Involved

| File | Role |
|------|------|
| `prompts/evaluator_prompt.txt` | Evaluator role, scoring categories, output schema, rule reference map |
| `prompts/optimizer_prompt.txt` | Optimizer 6-step algorithm, decision constraints, output schema |
| `prompts/system_prompt.txt` | Target of optimization — updated when `action_taken == PROMPT_MODIFIED` |
| `prompts/user_constraints.txt` | Immutable personal rules — never read or modified by the optimizer |
| `prompts/prompt_summary.txt` | Compressed rule reference for the evaluator (kept in sync with `system_prompt.txt`) |
| `prompts/daily_feedback.json` | Accumulator — array of evaluation results; cleared after each optimizer run |
| `prompts/change_tracker.json` | Scoring ledger — active rules, graveyard, archived, run log |
| `.claude/commands/judge-resume.md` | Slash command that invokes the evaluator |
| `.claude/commands/optimize-prompt.md` | Slash command that invokes the optimizer |

No automated tests. The loop is driven by LLM evaluation — correctness is verified by inspecting `daily_feedback.json` and `change_tracker.json` after each run.

---

## Purpose

The feedback loop is an automated prompt optimization system. Each time resumes are generated and evaluated, the evaluator scores them against the generation rules. After enough evaluations accumulate, the optimizer updates `system_prompt.txt` based on which rules are consistently failing or succeeding. Over time, the generation prompt improves without manual editing.

---

## Loop Overview

```
1. Generate resume  →  output/{Company}_Resume.tex + output/extras/{Company}_Resume.txt + _jd.txt
2. /judge-resume    →  scores resume, appends result to daily_feedback.json
3. (repeat 1–2 for N ≥ 5 resumes)
4. /optimize-prompt →  reads daily_feedback.json, updates system_prompt.txt + change_tracker.json
                       clears daily_feedback.json to []
```

---

## Evaluator (`evaluator_prompt.txt`)

### Inputs

The evaluator receives three inputs per run:
1. `prompt_summary` — compressed rule reference (not the full `system_prompt.txt`)
2. `resume_text` — plain-text extract from `output/extras/{Company}_Resume.txt`
3. `job_description` — JD snapshot from `output/extras/{Company}_jd.txt`

Using `prompt_summary.txt` instead of the full `system_prompt.txt` keeps the evaluator's context concise and focuses it on evaluable rules only.

### Scoring Categories

| Category | Weight | Primary Focus |
|----------|--------|---------------|
| `KEYWORD_ALIGNMENT` | 30% | JD tool coverage, keyword distribution, domain targeting, no stuffing |
| `BULLET_ARCHITECTURE` | 40% | Four-component structure, verb quality, banned vocab, structure variation |
| `BELIEVABILITY` | 20% | Metric defensibility, intern scope, skill-to-bullet consistency |
| `ACCOUNTABILITY` | 10% | Personal ownership, no team attribution, project motivation framing |

**Total score:** `(KEYWORD×0.30) + (BULLET×0.40) + (BELI×0.20) + (ACCT×0.10)`, rounded to nearest integer.

**Pass threshold:** `total_score >= 72`

### Rule Reference Map

Every rule has an exact identifier used in `failures[]` and `change_tracker.json`. Full list:

```
KEYWORD_ALIGNMENT:  KW_MINIMUM_COVERAGE, KW_DOMAIN_TARGETING, KW_TOP_THIRD_SIGNAL,
                    KW_MAX_TWO_SECTIONS_PER_KEYWORD, KW_DISTRIBUTION_SINGLE_TECH,
                    KW_DISTRIBUTION_MULTI_TECH, KW_ACRONYM_FULL_TERM, KW_NO_STUFFING

BULLET_ARCHITECTURE: BULLET_FOUR_COMPONENT, BULLET_STRONG_VERB, BULLET_NO_WEAK_VERB,
                     BULLET_METRIC_PRESENT, BULLET_MAX_180_CHARS, BULLET_NO_BANNED_VOCAB,
                     BULLET_STRUCTURE_VARIATION, BULLET_NO_SCAFFOLD_REPEAT,
                     BULLET_NO_WORD_REPEAT, BULLET_NO_OVER_PLUS

BELIEVABILITY:      BELI_METRIC_DEFENSIBLE, BELI_NO_100_PERCENT, BELI_NO_UPTIME_PERCENT,
                    BELI_NO_OVER_50_PERCENT, BELI_NO_LARGE_DATA_CLAIMS,
                    BELI_INTERN_NO_GREENFIELD, BELI_INTERN_NO_LEADERSHIP,
                    BELI_INTERN_SCOPE_REALISTIC, BELI_SKILL_TO_BULLET,
                    BELI_PROJECT_COMPLEXITY, BELI_LOCAL_HARDWARE_CONSTRAINT

ACCOUNTABILITY:     ACCT_NO_TEAM_ATTRIBUTION, ACCT_PERSONAL_OWNERSHIP,
                    ACCT_PROJECT_MOTIVATION_FIRST
```

### Output Schema

```json
{
  "resume_id": "Company_Resume",
  "total_score": 84,
  "passed": true,
  "category_scores": {
    "keyword_alignment": 88,
    "bullet_architecture": 82,
    "believability": 90,
    "accountability": 75
  },
  "failures": [
    {
      "rule_ref": "BULLET_MAX_180_CHARS",
      "category": "BULLET_ARCHITECTURE",
      "evidence": "3 bullets exceed 180 chars in experience section"
    }
  ],
  "constrained_failures": [
    {
      "rule_ref": "KW_TOP_THIRD_SIGNAL",
      "category": "KEYWORD_ALIGNMENT",
      "reason": "AWS section locked by user_constraints; cannot modify to match non-cloud JD"
    }
  ],
  "new_patterns": []
}
```

**Key output rules:**
- `failures[]` contains only rules that **fail** — absence signals a pass. The optimizer infers `pass_count` from silence.
- `constrained_failures[]` is for rules that fail due to immutable `user_constraints` — optimizer excludes these from scoring.
- `new_patterns[]` is always populated (empty array if none) — describes emerging failure patterns not governed by any existing rule.

---

## Optimizer (`optimizer_prompt.txt`)

### Inputs

The optimizer receives four inputs:
1. `SYSTEM_PROMPT` — current `system_prompt.txt`
2. `CHANGE_TRACKER` — current `change_tracker.json`
3. `DAILY_FEEDBACK` — array of evaluator outputs from `daily_feedback.json`
4. `RUN_METADATA` — `{ run_date, total_resumes_evaluated, jd_domains }`

### Step 1: Sample Gate

```
IF total_resumes_evaluated < minimum_sample_size (5):
    → set action_taken = "SKIPPED_INSUFFICIENT_SAMPLE"
    → update run_log only
    → return immediately (no scoring, no changes)
```

Prevents prompt changes based on insufficient signal. The `minimum_sample_size` threshold lives in `change_tracker.meta`.

### Step 2: Aggregate Feedback

For each `rule_ref` across all evaluated resumes:
```
fail_count        = resumes where rule_ref appears in failures[]
constrained_count = resumes where rule_ref appears in constrained_failures[]
evaluable_total   = total_resumes - constrained_count
pass_count        = evaluable_total - fail_count
```

### Step 3: Score Active Rules (TRACKING status)

For each rule in `change_tracker.active_changes` with `status = "TRACKING"`:

```
strength_delta = +15 × (pass_count / evaluable_total)
problem_delta  = -20 × (fail_count / evaluable_total)
net_delta      = strength_delta + problem_delta   (rounded to nearest int)
cumulative_score += net_delta
```

**Why asymmetric weights (+15 vs −20)?** Failures should penalize faster than successes reward — a broken rule should be flagged and removed before it corrupts enough outputs.

**Threshold actions:**
| Condition | Action |
|-----------|--------|
| `cumulative_score >= 70` | Status → `GRADUATED` (rule permanently locked, never reverted) |
| `cumulative_score <= -50` | Status → `GRAVEYARD/OBSERVING` (rule removed from system_prompt) |
| Mixed signal across 3+ consecutive runs | Flag `needs_rewrite = true` (rewrite in Step 5) |

**Revert types:**
- `WRONG_PREMISE` — all evidence entries are negative (rule never worked) → remove without replacement
- `NEEDS_REPLACEMENT` — some positive evidence (rule intent was right, implementation wrong) → generate revised version

### Step 4: Score Graveyard Rules (OBSERVING status)

Monitors whether removing a rule causes measurable quality degradation:

```
IF new_patterns or failures[] match what the removed rule was designed to prevent:
    absence_delta = +18 × (affected_resumes / total)
    absence_score += absence_delta

IF absence_score >= readd_threshold (60):
    status → READD_PENDING
```

**Calendar-day accounting:** The observation window is 14 calendar days from `date_removed`, not 14 optimizer runs. Days when no resumes are evaluated still count against the window.

If the window expires with `absence_score < 60` → rule moves from `graveyard` to `archived` (permanent removal).

### Step 5: Decide Prompt Changes (in order)

1. **REVERTS** — remove rule text from `system_prompt.txt`; if `NEEDS_REPLACEMENT`, write revised version and create new CHG entry with `parent_id`
2. **RE-ADDS** — write best version of rule back into `system_prompt.txt`; create new CHG entry; move graveyard entry to archived
3. **REWRITES** — write improved version in-place; create new CHG entry; move original to graveyard as `OBSERVING`
4. **NEW RULES** — max 2 new rules per run; create CHG entry with `type=ADDED, origin=NEW`; update verification checklist
5. **VERIFICATION CHECKLIST SYNC** — add/remove/update checklist items to mirror the full active rule set

**Hard decision constraints (never violated):**
- Never modify `formatting_constraints`, `output_requirements`, `priority_order`, or the `<example>` block
- Never modify `user_constraints.txt`
- Never revert a `GRADUATED` rule
- Never revert based on a single run unless `cumulative_score` is within 10 points of the threshold
- Maximum 2 new rules per run

### Step 6: Finalize Tracker

Updates `change_tracker.meta` (version, timestamps, total_runs), appends a `run_log` entry with score snapshots for all active rules, and sets `action_taken` to one of: `SKIPPED_INSUFFICIENT_SAMPLE | NO_CHANGE | SCORES_UPDATED | PROMPT_MODIFIED`.

---

## `change_tracker.json` Structure

```json
{
  "meta": {
    "version": "1.0",
    "system_prompt_version": "v4",
    "minimum_sample_size": 5,
    "graduate_threshold": 70,
    "revert_threshold": -50,
    "readd_threshold": 60,
    "observation_window_days": 14,
    "max_new_rules_per_run": 2,
    "base_weights": { "strength": 15, "problem": 20, "absence": 18 }
  },
  "active_changes": [
    {
      "id": "CHG-001",
      "type": "ADDED",
      "version_introduced": "v2",
      "block": "bullet_point_rules",
      "rule_ref": "BULLET_FOUR_COMPONENT",
      "description": "Every bullet must follow four-component architecture",
      "cumulative_score": 0,
      "evaluations_seen": 0,
      "status": "TRACKING",
      "evidence": []
    }
  ],
  "graveyard": [
    {
      "id": "CHG-G01",
      "status": "OBSERVING",
      "observation_deadline": "2026-04-02",
      "absence_score": 0,
      "removal_reason": "NEEDS_REPLACEMENT",
      "replaced_by": "CHG-007",
      "evidence": []
    }
  ],
  "archived": [],
  "run_log": []
}
```

**Evidence entries** appended each run:
```json
{
  "date": "2026-04-01",
  "delta": -8,
  "pass_count": 3,
  "fail_count": 2,
  "constrained_count": 0,
  "evaluable_total": 5,
  "summary": "BULLET_FOUR_COMPONENT: 2 failures across 5 resumes"
}
```

---

## `daily_feedback.json`

Simple accumulator — an array of evaluator output objects, one per evaluated resume. Grows until `/optimize-prompt` runs, then cleared unconditionally to `[]`.

```json
[
  { "resume_id": "Google_Resume", "total_score": 82, "passed": true, "failures": [...], ... },
  { "resume_id": "Meta_Resume",   "total_score": 74, "passed": true, "failures": [...], ... }
]
```

---

## `prompt_summary.txt`

A compressed version of the active rules in `system_prompt.txt`, provided to the evaluator in place of the full file. It contains the same rule set organized under `<bullet_rules>`, `<keyword_rules>`, `<quality_rules>`, `<skill_consistency_rule>`, `<realism_rules>`, and `<immutable_constraints>` tags.

Kept in sync with `system_prompt.txt` by the optimizer — whenever a rule is added, removed, or rewritten in `system_prompt.txt`, the corresponding entry in `prompt_summary.txt` is updated.

The evaluator uses `prompt_summary.txt` rather than the full prompt to reduce context size and ensure it evaluates only rules that are semantically evaluable (not structural constraints like `\begin{document}` delimiters).
