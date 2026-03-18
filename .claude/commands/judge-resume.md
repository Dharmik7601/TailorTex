---
description: Score a resume for AI-generation likelihood using the ai_judge rubric
argument-hint: [resume prefix e.g. Google_SWE]
allowed-tools: Read
---

Evaluate the resume `output/$ARGUMENTS_Resume.pdf` for AI-generation likelihood.

Follow these steps exactly:

1. Read `output/$ARGUMENTS_Resume.pdf` — this is the resume to evaluate
2. Read `prompts/ai_judge.txt` — this contains the role, scoring rules, and output requirements
3. Apply the scoring rubric from `prompts/ai_judge.txt` to the resume content
4. Output a single JSON string to stdout following the format in `prompts/ai_judge.txt`
