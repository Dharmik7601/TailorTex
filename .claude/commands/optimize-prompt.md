---
description: Analyze daily evaluation feedback and optimize system_prompt, change_tracker, and prompt_summary
allowed-tools: Read, Write
---

> Allowed file operations: Read any file listed in the steps below. Write ONLY to `prompts/system_prompt.txt`, `prompts/change_tracker.json`, `prompts/prompt_summary.txt`, and `prompts/daily_feedback.json` as explicitly permitted by the steps below. Do not create any new files or write to any other path under any circumstance.

Run the prompt optimization loop using today's evaluation feedback.

Follow these steps exactly:

1. Read `prompts/system_prompt.txt` — the current generation prompt to optimize
2. Read `prompts/user_constraints.txt` — immutable personal config (read-only reference, never modify)
3. Read `prompts/change_tracker.json` — the scoring ledger and rule history
4. Read `prompts/daily_feedback.json` — the array of evaluation results from today's judge runs
5. Read `prompts/optimizer_prompt.txt` — the full optimization rules, decision constraints, and output schema
6. Read `prompts/prompt_summary.txt` — the compressed rule reference used by the evaluator

7. Construct the four inputs the optimizer expects:
   - SYSTEM_PROMPT: content of `prompts/system_prompt.txt`
   - CHANGE_TRACKER: content of `prompts/change_tracker.json`
   - DAILY_FEEDBACK: the array from `prompts/daily_feedback.json`
   - RUN_METADATA: a JSON object with:
     - `run_date`: today's date in YYYY-MM-DD format
     - `total_resumes_evaluated`: the number of objects in the daily_feedback array
     - `jd_domains`: an inferred list of JD domains based on the `resume_id` values in daily_feedback (e.g. ["SWE", "ML", "Data Engineering"])

8. Apply the optimizer logic from `prompts/optimizer_prompt.txt` to the four inputs above. Follow all steps (sample gate, aggregate feedback, score active tracking, score graveyard, decide prompt changes, finalize tracker) exactly as defined.

9. Write the updated system prompt returned in `updated_system_prompt` back to `prompts/system_prompt.txt` — only if the optimizer's `action_taken` is `PROMPT_MODIFIED`. If `action_taken` is `NO_CHANGE` or `SCORES_UPDATED` or `SKIPPED_INSUFFICIENT_SAMPLE`, do NOT overwrite the file.

10. Write the updated change tracker returned in `updated_change_tracker` back to `prompts/change_tracker.json` — always, regardless of `action_taken`.

11. If `action_taken` is `PROMPT_MODIFIED` (i.e., `prompts/system_prompt.txt` was updated):
    - Compare the new system prompt to the previous version
    - For every rule that was added, removed, or rewritten, update `prompts/prompt_summary.txt` accordingly:
      - Added rule: append the new rule to the appropriate rule block in prompt_summary.txt using the same `RULE_REF: description` format as the existing entries
      - Removed rule (reverted without replacement): delete its line from prompt_summary.txt
      - Rewritten rule: replace its existing line in prompt_summary.txt with the updated description
    - Write the updated content back to `prompts/prompt_summary.txt`
    - If no rules changed in a way that affects prompt_summary.txt, do not write the file

12. Clear `prompts/daily_feedback.json` by writing an empty JSON array `[]` to it — do this unconditionally as the final step, even if the optimizer was skipped due to insufficient sample size.

13. Print a brief plain-text summary to stdout covering:
    - `action_taken` value
    - `total_resumes_evaluated`
    - Each entry in `changes_made` (chg_id, change_type, rule_ref, description)
    - Any `deferred_patterns`
    - Whether `prompt_summary.txt` was updated
