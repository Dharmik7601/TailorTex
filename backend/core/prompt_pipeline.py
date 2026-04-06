import os
import re
from dataclasses import dataclass
from typing import Callable, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class PipelineOutput:
    system_prompt: str
    user_prompt: str
    preamble: str
    raw_job_description: str  # preserved so ClaudeCliProvider can write job_description.txt


def build_prompts(
    master_resume_tex: str,
    job_description: str,
    use_constraints: bool = True,
    use_projects: bool = True,
    log: Optional[Callable[[str], None]] = None,
) -> PipelineOutput:
    """
    Pure domain logic: splits preamble, loads prompt files, assembles final prompts.
    Does not call any AI API.
    """
    if log is None:
        log = print

    delimiter = r"\begin{document}"
    if delimiter not in master_resume_tex:
        raise ValueError(r"\begin{document} not found in the provided .tex file.")

    preamble, body_rest = master_resume_tex.split(delimiter, 1)
    resume_body = delimiter + body_rest

    # Load system prompt
    prompt_path = os.path.join(BASE_DIR, "prompts", "system_prompt.txt")
    if not os.path.exists(prompt_path):
        raise ValueError(f"System prompt not found at {prompt_path}")
    with open(prompt_path, "r", encoding="utf-8") as f:
        system_prompt = f.read()

    # Conditionally append constraints
    # NOTE: constraints file already contains its own <constraints> XML tags
    if use_constraints:
        constraints_path = os.path.join(BASE_DIR, "prompts", "user_constraints.txt")
        if os.path.exists(constraints_path):
            with open(constraints_path, "r", encoding="utf-8") as f:
                system_prompt += f"\n\n{f.read()}\n"
        else:
            log("Warning: user_constraints.txt not found, skipping.")

    # Conditionally append extra projects
    # NOTE: projects file already contains its own <project_bank> XML tags
    if use_projects:
        projects_path = os.path.join(BASE_DIR, "prompts", "additional_projects.txt")
        if os.path.exists(projects_path):
            with open(projects_path, "r", encoding="utf-8") as f:
                system_prompt += f"\n\n{f.read()}\n"
        else:
            log("Warning: additional_projects.txt not found, skipping.")

    # ORDER MATTERS — resume body first (bulk data, lower attention zone),
    # job description near the end (high recency attention),
    # explicit task instruction last (maximum recency attention).
    user_prompt = f"""<resume_body>
                    {resume_body}
                    </resume_body>

                    <job_description>
                    {job_description}
                    </job_description>

                    <task>
                    Using the rules in the system prompt, rewrite the resume body above to match this job description. Return only the raw LaTeX from \\begin{{document}} to \\end{{document}}.
                </task>"""

    return PipelineOutput(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        preamble=preamble,
        raw_job_description=job_description,
    )


def postprocess_latex(raw_latex: str) -> str:
    """Strip markdown fences, fix bold escaping, fix blank-line spacing before \\resumeItem."""
    clean = _extract_latex(raw_latex)
    # Convert stray markdown bold (**text**) to LaTeX \textbf{text}
    clean = re.sub(r'\*\*(.+?)\*\*', r'\\textbf{\1}', clean)
    # Remove blank lines before \resumeItem entries (blank lines cause paragraph breaks in LaTeX)
    while re.search(r'\n[ \t]*\n[ \t]*(?=\\resumeItem)', clean):
        clean = re.sub(r'\n[ \t]*\n([ \t]*\\resumeItem)', r'\n\1', clean)
    return clean


def validate_latex(latex: str) -> None:
    """Raise ValueError if the output is missing document delimiters (likely truncated)."""
    if r"\begin{document}" not in latex or r"\end{document}" not in latex:
        raise ValueError(
            r"LLM output is missing \begin{document} or \end{document}. Response may be truncated."
        )


def _extract_latex(text: str) -> str:
    """Strips away markdown wrappers if the LLM includes them."""
    pattern = r"```(?:latex|tex)?\n(.*?)```"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text.strip()
