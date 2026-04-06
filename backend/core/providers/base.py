from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable


@dataclass
class GenerationRequest:
    """Everything a provider needs to generate a tailored resume."""
    system_prompt: str           # fully assembled system prompt (with constraints/projects)
    user_prompt: str             # fully assembled user prompt (resume body + JD + task)
    company_name: str            # used for output filename
    preamble: str                # LaTeX preamble — prepended to LLM output before saving
    raw_job_description: str     # raw JD text — used by ClaudeCliProvider to write job_description.txt
    log: Callable[[str], None]   # log callback


@dataclass
class GenerationResult:
    """Output from a provider after generation and compilation."""
    tex_path: str
    pdf_path: str


class ResumeProvider(ABC):
    """
    Strategy interface for AI resume generation providers.

    Implementations are responsible for:
      - Calling the underlying AI model (API or subprocess)
      - Extracting valid LaTeX from the response
      - Saving the .tex file and compiling to PDF
      - Returning a GenerationResult on success, raising RuntimeError on failure

    Implementations are NOT responsible for:
      - Prompt construction (handled by prompt_pipeline.build_prompts)
      - Preamble splitting (handled by prompt_pipeline.build_prompts)
      - Post-processing shared LaTeX artifacts (handled by prompt_pipeline helpers)
    """

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Stable string identifier, e.g. 'gemini', 'claudecli'. Used as the queue dict key."""
        ...

    @abstractmethod
    def generate(self, request: GenerationRequest) -> GenerationResult:
        """
        Execute generation synchronously.
        Must raise RuntimeError on failure — the message will be logged.
        """
        ...
