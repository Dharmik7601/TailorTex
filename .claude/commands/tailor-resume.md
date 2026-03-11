---
description: Generate a tailored LaTeX resume for the current job description
argument-hint: [output-filename e.g. Google_SWE]
allowed-tools: Read, Write, Bash
---

Generate a tailored LaTeX resume. Output filename prefix: $ARGUMENTS

Follow these steps exactly:

1. Read `Master_Resume.tex` — split at `\begin{document}` and keep the preamble separate
2. Read `job_description-claude.txt` for the target role
3. Read `prompts/system_prompt.txt` for the modification rules and whitelist
4. If `prompts/user_constraints.txt` is non-empty, apply those constraints too
5. If `prompts/additional_projects.txt` is non-empty, use it as the project bank
6. Rewrite only the resume body following all rules — only modify `\footnotesize{...}`, `\resumeItem{...}`, and `\textbf{...}` macros. Do NOT alter the preamble or LaTeX structure
7. Guarantee the resume fits one page
8. Reassemble the full `.tex` by prepending the original preamble
9. Write the complete file to `output/$ARGUMENTS_Resume.tex`
10. Compile: `pdflatex -interaction=nonstopmode -output-directory=output output/$ARGUMENTS_Resume.tex`
11. Clean up: delete `output/$ARGUMENTS_Resume.aux`, `output/$ARGUMENTS_Resume.log`, `output/$ARGUMENTS_Resume.out`
13. Confirm both `output/$ARGUMENTS_Resume.tex` and `output/$ARGUMENTS_Resume.pdf` exist
12. Open the PDF: `start output/$ARGUMENTS_Resume.pdf`
