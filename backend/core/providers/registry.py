from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelConfig:
    """
    Declarative capabilities and call-site settings for a single LLM model.

    Adding a new model = one line in the relevant model chain below.
    Changing fallback order = reorder entries in the list.
    """
    name: str
    supports_system_instruction: bool
    # If supports_system_instruction is False, system and user prompts are merged
    # into a single message using this template before sending to the API.
    merge_system_template: str = "<system>\n{system}\n</system>\n\n{user}"
    temperature: float = 0.2


# Ordered list — tried from first to last; first success wins (waterfall fallback).
# To add a new Google model: append a ModelConfig entry here.
GEMINI_MODEL_CHAIN: list[ModelConfig] = [
    ModelConfig(
        name="gemma-4-31b-it",
        supports_system_instruction=False,   # Gemma does not support system_instruction
    ),
    ModelConfig(
        name="gemini-3-flash-preview",
        supports_system_instruction=True,    # Gemini supports system_instruction natively
        temperature=0.2,
    ),
]
