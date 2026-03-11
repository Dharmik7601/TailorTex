import os
import re
import sys
from typing import Callable, Optional
from google import genai
from google.genai import types
from dotenv import load_dotenv
from core.compiler import compile_latex

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def generate_resume(
    master_resume_tex: str,
    job_description: str,
    company_name: str,
    use_constraints: bool = True,
    use_projects: bool = True,
    log_callback: Optional[Callable[[str], None]] = None,
) -> tuple[str, str]:
    """
    Generates a tailored LaTeX resume and compiles it to PDF.

    Returns:
        (output_tex_path, output_pdf_path)

    Raises:
        ValueError: on invalid input or LLM output
        RuntimeError: on compilation failure
    """
    if log_callback is None:
        log_callback = print

    # Split preamble to save tokens
    delimiter = r"\begin{document}"
    if delimiter not in master_resume_tex:
        raise ValueError(f"\\begin{{document}} not found in the provided .tex file.")

    parts = master_resume_tex.split(delimiter, 1)
    preamble = parts[0]
    resume_body = delimiter + parts[1]

    # Load system prompt
    prompt_path = os.path.join(BASE_DIR, "prompts", "system_prompt.txt")
    if not os.path.exists(prompt_path):
        raise ValueError(f"System prompt not found at {prompt_path}")
    with open(prompt_path, "r", encoding="utf-8") as f:
        system_prompt = f.read()

    # Conditionally append constraints
    if use_constraints:
        constraints_path = os.path.join(BASE_DIR, "prompts", "user_constraints.txt")
        if os.path.exists(constraints_path):
            with open(constraints_path, "r", encoding="utf-8") as f:
                system_prompt += f"\n\nUSER REQUIREMENTS & CONSTRAINTS\n{f.read()}\n"
        else:
            log_callback("Warning: user_constraints.txt not found, skipping.")

    # Conditionally append extra projects
    if use_projects:
        projects_path = os.path.join(BASE_DIR, "prompts", "additional_projects.txt")
        if os.path.exists(projects_path):
            with open(projects_path, "r", encoding="utf-8") as f:
                system_prompt += f"\n\nADDITIONAL USER PROJECTS\nYou may use these projects directly or modify them to align with the job description:\n{f.read()}\n"
        else:
            log_callback("Warning: additional_projects.txt not found, skipping.")

    user_prompt = f"Job Description:\n{job_description}\n\n---\nMaster Resume Body (LaTeX):\n{resume_body}\n"

    llm_output = _call_gemini(system_prompt, user_prompt, log_callback)

    clean_latex = _extract_latex(llm_output)

    # Convert any stray markdown bold (**text**) to LaTeX \textbf{text}
    clean_latex = re.sub(r'\*\*(.+?)\*\*', r'\\textbf{\1}', clean_latex)

    # Remove blank lines before \resumeItem entries (blank lines cause paragraph breaks in LaTeX)
    # Use \s*\n to catch lines with only whitespace, and loop to handle multiple consecutive blank lines
    while re.search(r'\n[ \t]*\n[ \t]*(?=\\resumeItem)', clean_latex):
        clean_latex = re.sub(r'\n[ \t]*\n([ \t]*\\resumeItem)', r'\n\1', clean_latex)

    if r"\begin{document}" not in clean_latex or r"\end{document}" not in clean_latex:
        raise ValueError("LLM output is missing \\begin{document} or \\end{document}. Response may be truncated.")

    # Save .tex
    output_dir = os.path.join(BASE_DIR, "output")
    os.makedirs(output_dir, exist_ok=True)

    output_tex_path = os.path.join(output_dir, f"{company_name}_Resume.tex")
    with open(output_tex_path, "w", encoding="utf-8") as f:
        f.write(preamble + "\n" + clean_latex)
    log_callback(f"Saved generated LaTeX to {output_tex_path}")

    # Compile to PDF
    log_callback("Compiling LaTeX to PDF...")
    compile_latex(output_tex_path, output_dir, log_callback=log_callback)

    output_pdf_path = os.path.join(output_dir, f"{company_name}_Resume.pdf")
    log_callback("Done!")

    return output_tex_path, output_pdf_path


def _call_gemini(system_prompt: str, user_prompt: str, log_callback) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key) if api_key else genai.Client()
    models_to_try = ["gemini-2.5-flash-preview-05-20", "gemini-2.5-flash"]
    last_error = None
    for model_name in models_to_try:
        try:
            log_callback(f"Trying model: {model_name}...")
            response = client.models.generate_content(
                model=model_name,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.2,
                ),
            )
            log_callback(f"Successfully generated content using {model_name}.")
            return response.text
        except Exception as e:
            last_error = e
            log_callback(f"Model {model_name} failed: {e}. Attempting fallback...")
    raise RuntimeError(f"All Gemini models failed. Last error: {last_error}")


def _extract_latex(text: str) -> str:
    """Strips away markdown wrappers if the LLM includes them."""
    pattern = r"```(?:latex|tex)?\n(.*?)```"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text.strip()
