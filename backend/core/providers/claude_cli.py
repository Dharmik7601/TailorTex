import os
import subprocess

from core.compiler import compile_latex
from core.providers.base import GenerationRequest, GenerationResult, ResumeProvider

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class ClaudeCliProvider(ResumeProvider):
    """
    Invokes the Claude Code CLI via subprocess.

    The CLI reads job_description.txt and writes output/{company}_Resume.tex itself
    (via the /tailor-resume slash command). This provider:
      1. Writes raw_job_description to job_description.txt
      2. Runs: claude -p /tailor-resume {company_name}
      3. Validates that the .tex file was produced
      4. Compiles it to PDF and returns the paths

    Note: system_prompt and user_prompt from GenerationRequest are not used here —
    Claude reads those files internally via the slash command.
    """

    @property
    def provider_id(self) -> str:
        return "claudecli"

    def generate(self, request: GenerationRequest) -> GenerationResult:
        jd_path = os.path.join(BASE_DIR, "job_description.txt")
        with open(jd_path, "w", encoding="utf-8") as f:
            f.write(request.raw_job_description)

        request.log(f"Running Claude Code pipeline for {request.company_name}...")
        result = subprocess.run(
            ["claude", "-p", f"/tailor-resume {request.company_name}"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
        )

        if result.stdout:
            for line in result.stdout.splitlines():
                request.log(line)
        if result.returncode != 0:
            err = result.stderr.strip() or "claude -p exited with non-zero status"
            raise RuntimeError(err)

        output_dir = os.path.join(BASE_DIR, "output")
        tex_path = os.path.join(output_dir, f"{request.company_name}_Resume.tex")
        if not os.path.exists(tex_path):
            raise RuntimeError(f"TeX file not found at {tex_path} after Claude run")

        request.log("Compiling LaTeX to PDF...")
        compile_latex(tex_path, output_dir, log_callback=request.log)

        pdf_path = os.path.join(output_dir, f"{request.company_name}_Resume.pdf")
        if not os.path.exists(pdf_path):
            raise RuntimeError(f"PDF not found at {pdf_path} after compilation")

        request.log("Done!")
        return GenerationResult(tex_path=tex_path, pdf_path=pdf_path)
