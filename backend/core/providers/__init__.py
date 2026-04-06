from core.providers.base import GenerationRequest, GenerationResult, ResumeProvider
from core.providers.claude_cli import ClaudeCliProvider
from core.providers.gemini import GeminiProvider

# ---------------------------------------------------------------------------
# Provider registry
#
# Maps provider_id -> provider instance.
# To add a new provider:
#   1. Create backend/core/providers/<name>.py with a class implementing ResumeProvider
#   2. Import it here and call _register(MyProvider())
#   3. Done — the queue worker is created automatically, no other files change.
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, ResumeProvider] = {}


def _register(provider: ResumeProvider) -> None:
    _REGISTRY[provider.provider_id] = provider


_register(GeminiProvider())
_register(ClaudeCliProvider())


def get_provider(provider_id: str) -> ResumeProvider:
    """Return the provider for the given ID. Falls back to 'gemini' for unknown IDs."""
    return _REGISTRY.get(provider_id, _REGISTRY["gemini"])


def registered_provider_ids() -> list[str]:
    """Return all registered provider IDs. Used by server.py to build _work_queues."""
    return list(_REGISTRY.keys())
