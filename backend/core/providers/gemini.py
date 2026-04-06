import os

from dotenv import load_dotenv
from google import genai
from google.genai import types

from core.compiler import compile_latex
from core.prompt_pipeline import postprocess_latex, validate_latex
from core.providers.base import GenerationRequest, GenerationResult, ResumeProvider
from core.providers.registry import GEMINI_MODEL_CHAIN

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class GeminiProvider(ResumeProvider):
    """
    Calls the Google Generative AI API with a waterfall fallback across GEMINI_MODEL_CHAIN.
    Model-specific quirks (e.g. Gemma not supporting system_instruction) are handled
    declaratively via ModelConfig — no if/else branching on model names.
    """

    @property
    def provider_id(self) -> str:
        return "gemini"

    def generate(self, request: GenerationRequest) -> GenerationResult:
        raw = self._call_with_fallback(request)
        clean = postprocess_latex(raw)
        validate_latex(clean)
        return self._save_and_compile(request, clean)

    def _call_with_fallback(self, request: GenerationRequest) -> str:
        api_key = os.environ.get("GEMINI_API_KEY")
        client = genai.Client(api_key=api_key) if api_key else genai.Client()

        last_error = None
        for model_cfg in GEMINI_MODEL_CHAIN:
            try:
                request.log(f"Trying model: {model_cfg.name}...")
                if not model_cfg.supports_system_instruction:
                    contents = model_cfg.merge_system_template.format(
                        system=request.system_prompt,
                        user=request.user_prompt,
                    )
                    response = client.models.generate_content(
                        model=model_cfg.name,
                        contents=contents,
                    )
                else:
                    response = client.models.generate_content(
                        model=model_cfg.name,
                        contents=request.user_prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=request.system_prompt,
                            temperature=model_cfg.temperature,
                        ),
                    )
                request.log(f"Successfully generated content using {model_cfg.name}.")
                return response.text
            except Exception as e:
                last_error = e
                request.log(f"Model {model_cfg.name} failed: {e}. Attempting fallback...")

        raise RuntimeError(f"All Gemini models failed. Last error: {last_error}")

    def _save_and_compile(self, request: GenerationRequest, clean_latex: str) -> GenerationResult:
        output_dir = os.path.join(BASE_DIR, "output")
        os.makedirs(output_dir, exist_ok=True)

        tex_path = os.path.join(output_dir, f"{request.company_name}_Resume.tex")
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(request.preamble + "\n" + clean_latex)
        request.log(f"Saved generated LaTeX to {tex_path}")

        request.log("Compiling LaTeX to PDF...")
        compile_latex(tex_path, output_dir, log_callback=request.log)

        pdf_path = os.path.join(output_dir, f"{request.company_name}_Resume.pdf")
        request.log("Done!")
        return GenerationResult(tex_path=tex_path, pdf_path=pdf_path)
