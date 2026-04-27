"""
Unit tests for core/providers/

Covers:
  - registry.py:    ModelConfig immutability, GEMINI_MODEL_CHAIN structure
  - base.py:        GenerationRequest / GenerationResult field presence
  - __init__.py:    get_provider(), registered_provider_ids(), fallback behavior
  - gemini.py:      GeminiProvider — model chain iteration, system_instruction branching,
                    fallback on failure, RuntimeError when all models fail, save+compile flow
  - claude_cli.py:  ClaudeCliProvider — JD file write, subprocess invocation, error paths
"""

import dataclasses
import os
import sys

import pytest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.providers.base import GenerationRequest, GenerationResult, ResumeProvider
from core.providers.registry import GEMINI_MODEL_CHAIN, ModelConfig
from core.providers import get_provider, registered_provider_ids
from core.providers.gemini import GeminiProvider
from core.providers.claude_cli import ClaudeCliProvider


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_request(**overrides) -> GenerationRequest:
    defaults = dict(
        system_prompt="You are a resume writer.",
        user_prompt="<resume_body>body</resume_body><job_description>JD</job_description>",
        company_name="Acme",
        preamble=r"\documentclass{article}",
        raw_job_description="JD text",
        log=lambda msg: None,
    )
    defaults.update(overrides)
    return GenerationRequest(**defaults)


VALID_LATEX = r"\begin{document}\resumeItem{Did things.}\end{document}"


# ═══════════════════════════════════════════════════════════════════════════════
# ModelConfig / registry.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestModelConfig:
    def test_frozen_cannot_be_mutated(self):
        cfg = ModelConfig(name="test-model", supports_system_instruction=True)
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError, AttributeError)):
            cfg.name = "other"

    def test_default_temperature(self):
        cfg = ModelConfig(name="x", supports_system_instruction=True)
        assert cfg.temperature == 0.2

    def test_default_merge_template_contains_placeholders(self):
        cfg = ModelConfig(name="x", supports_system_instruction=False)
        assert "{system}" in cfg.merge_system_template
        assert "{user}" in cfg.merge_system_template

    def test_merge_template_formats_correctly(self):
        cfg = ModelConfig(name="x", supports_system_instruction=False)
        result = cfg.merge_system_template.format(system="SYS", user="USR")
        assert "SYS" in result
        assert "USR" in result


class TestGeminiModelChain:
    def test_chain_has_at_least_two_entries(self):
        assert len(GEMINI_MODEL_CHAIN) >= 2

    def test_first_entry_does_not_support_system_instruction(self):
        # Gemma is first; it does not support system_instruction
        assert GEMINI_MODEL_CHAIN[0].supports_system_instruction is False

    def test_second_entry_supports_system_instruction(self):
        # Gemini is the fallback; it supports system_instruction natively
        assert GEMINI_MODEL_CHAIN[1].supports_system_instruction is True

    def test_all_entries_have_non_empty_names(self):
        for cfg in GEMINI_MODEL_CHAIN:
            assert cfg.name.strip() != ""

    def test_entries_are_model_config_instances(self):
        for cfg in GEMINI_MODEL_CHAIN:
            assert isinstance(cfg, ModelConfig)


# ═══════════════════════════════════════════════════════════════════════════════
# base.py — dataclass contracts
# ═══════════════════════════════════════════════════════════════════════════════

class TestGenerationRequest:
    def test_has_required_fields(self):
        req = _make_request()
        assert req.system_prompt
        assert req.user_prompt
        assert req.company_name
        assert req.preamble is not None
        assert req.raw_job_description is not None
        assert callable(req.log)

    def test_log_callback_is_callable(self):
        logs = []
        req = _make_request(log=logs.append)
        req.log("hello")
        assert logs == ["hello"]


class TestGenerationResult:
    def test_has_tex_and_pdf_paths(self):
        result = GenerationResult(tex_path="/out/Acme.tex", pdf_path="/out/Acme.pdf")
        assert result.tex_path == "/out/Acme.tex"
        assert result.pdf_path == "/out/Acme.pdf"


class TestResumeProviderInterface:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            ResumeProvider()

    def test_concrete_subclass_must_implement_provider_id_and_generate(self):
        class Incomplete(ResumeProvider):
            pass

        with pytest.raises(TypeError):
            Incomplete()

    def test_concrete_subclass_works_when_both_methods_implemented(self):
        class Stub(ResumeProvider):
            @property
            def provider_id(self):
                return "stub"

            def generate(self, request):
                return GenerationResult(tex_path="a.tex", pdf_path="a.pdf")

        s = Stub()
        assert s.provider_id == "stub"


# ═══════════════════════════════════════════════════════════════════════════════
# __init__.py — provider registry
# ═══════════════════════════════════════════════════════════════════════════════

class TestProviderRegistry:
    def test_registered_ids_includes_gemini(self):
        assert "gemini" in registered_provider_ids()

    def test_registered_ids_includes_claudecli(self):
        assert "claudecli" in registered_provider_ids()

    def test_get_provider_gemini_returns_gemini_provider(self):
        assert isinstance(get_provider("gemini"), GeminiProvider)

    def test_get_provider_claudecli_returns_claudecli_provider(self):
        assert isinstance(get_provider("claudecli"), ClaudeCliProvider)

    def test_get_provider_unknown_falls_back_to_gemini(self):
        assert isinstance(get_provider("nonexistent_provider"), GeminiProvider)

    def test_provider_ids_are_stable_strings(self):
        for pid in registered_provider_ids():
            assert isinstance(pid, str)
            assert len(pid) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# GeminiProvider
# ═══════════════════════════════════════════════════════════════════════════════

class TestGeminiProviderMetadata:
    def test_provider_id(self):
        assert GeminiProvider().provider_id == "gemini"


class TestGeminiProviderCallChain:
    """Tests for _call_with_fallback — model selection and API call shape."""

    def _mock_client(self, response_text="RESPONSE"):
        mock_response = MagicMock()
        mock_response.text = response_text
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        return mock_client

    @patch("core.providers.gemini.genai.Client")
    def test_uses_first_model_on_success(self, MockClient):
        client = self._mock_client(VALID_LATEX)
        MockClient.return_value = client

        logs = []
        req = _make_request(log=logs.append)
        provider = GeminiProvider()
        raw = provider._call_with_fallback(req)

        assert raw == VALID_LATEX
        assert client.models.generate_content.call_count == 1
        call_kwargs = client.models.generate_content.call_args
        assert call_kwargs[1]["model"] == GEMINI_MODEL_CHAIN[0].name or \
               call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("model") == GEMINI_MODEL_CHAIN[0].name

    @patch("core.providers.gemini.genai.Client")
    def test_falls_back_to_second_model_when_first_fails(self, MockClient):
        mock_response = MagicMock()
        mock_response.text = VALID_LATEX
        client = MagicMock()
        # First call raises, second succeeds
        client.models.generate_content.side_effect = [
            RuntimeError("rate limit"),
            mock_response,
        ]
        MockClient.return_value = client

        logs = []
        req = _make_request(log=logs.append)
        result = GeminiProvider()._call_with_fallback(req)

        assert result == VALID_LATEX
        assert client.models.generate_content.call_count == 2
        assert any("failed" in m.lower() or "fallback" in m.lower() for m in logs)

    @patch("core.providers.gemini.genai.Client")
    def test_raises_runtime_error_when_all_models_fail(self, MockClient):
        client = MagicMock()
        client.models.generate_content.side_effect = RuntimeError("always fails")
        MockClient.return_value = client

        req = _make_request()
        with pytest.raises(RuntimeError, match="All Gemini models failed"):
            GeminiProvider()._call_with_fallback(req)

    @patch("core.providers.gemini.genai.Client")
    def test_non_system_instruction_model_merges_prompts(self, MockClient):
        """Models with supports_system_instruction=False must receive merged content."""
        assert not GEMINI_MODEL_CHAIN[0].supports_system_instruction, \
            "Test assumes first chain entry does not support system_instruction"

        client = self._mock_client(VALID_LATEX)
        MockClient.return_value = client

        req = _make_request(system_prompt="SYSTEM", user_prompt="USER")
        GeminiProvider()._call_with_fallback(req)

        call_kwargs = client.models.generate_content.call_args[1]
        contents = call_kwargs.get("contents", "")
        assert "SYSTEM" in contents
        assert "USER" in contents
        # Must NOT pass config with system_instruction for this model
        assert "config" not in call_kwargs or call_kwargs.get("config") is None

    @patch("core.providers.gemini.genai.Client")
    def test_system_instruction_model_passes_config(self, MockClient):
        """Models with supports_system_instruction=True must pass system_instruction via config."""
        # Force the first model to fail so we reach the second (system_instruction model)
        mock_response = MagicMock()
        mock_response.text = VALID_LATEX
        client = MagicMock()
        client.models.generate_content.side_effect = [
            RuntimeError("first fails"),
            mock_response,
        ]
        MockClient.return_value = client

        req = _make_request(system_prompt="SYSTEM", user_prompt="USER")
        GeminiProvider()._call_with_fallback(req)

        # Second call should have used system_instruction
        second_call = client.models.generate_content.call_args_list[1]
        call_kwargs = second_call[1]
        assert "config" in call_kwargs
        assert call_kwargs["config"].system_instruction == "SYSTEM"


class TestGeminiProviderGenerate:
    """Tests for the full generate() method — post-processing, validation, file I/O."""

    def _patched_generate(self, tmp_path, raw_llm_output, monkeypatch):
        import core.providers.gemini as gemini_module
        monkeypatch.setattr(gemini_module, "BASE_DIR", str(tmp_path))
        (tmp_path / "output").mkdir(exist_ok=True)

        provider = GeminiProvider()
        with patch.object(provider, "_call_with_fallback", return_value=raw_llm_output), \
             patch("core.providers.gemini.compile_latex") as mock_compile:
            req = _make_request(company_name="TestCo", preamble=r"\documentclass{article}")
            result = provider.generate(req)
        return result, mock_compile

    def test_generate_returns_correct_paths(self, tmp_path, monkeypatch):
        result, _ = self._patched_generate(tmp_path, VALID_LATEX, monkeypatch)
        assert result.tex_path.endswith("TestCo_Resume.tex")
        assert result.pdf_path.endswith("TestCo_Resume.pdf")

    def test_generate_writes_tex_file(self, tmp_path, monkeypatch):
        self._patched_generate(tmp_path, VALID_LATEX, monkeypatch)
        tex_path = tmp_path / "output" / "TestCo_Resume.tex"
        assert tex_path.exists()

    def test_generate_tex_file_includes_preamble(self, tmp_path, monkeypatch):
        self._patched_generate(tmp_path, VALID_LATEX, monkeypatch)
        content = (tmp_path / "output" / "TestCo_Resume.tex").read_text(encoding="utf-8")
        assert r"\documentclass{article}" in content

    def test_generate_calls_compile_latex(self, tmp_path, monkeypatch):
        _, mock_compile = self._patched_generate(tmp_path, VALID_LATEX, monkeypatch)
        mock_compile.assert_called_once()

    def test_generate_raises_on_invalid_latex(self, tmp_path, monkeypatch):
        import core.providers.gemini as gemini_module
        monkeypatch.setattr(gemini_module, "BASE_DIR", str(tmp_path))
        (tmp_path / "output").mkdir(exist_ok=True)

        provider = GeminiProvider()
        with patch.object(provider, "_call_with_fallback", return_value="truncated output"), \
             patch("core.providers.gemini.compile_latex"):
            with pytest.raises(ValueError, match=r"\\begin\{document\}|\\end\{document\}"):
                provider.generate(_make_request(company_name="TestCo"))

    def test_generate_strips_markdown_fence(self, tmp_path, monkeypatch):
        """LLM output wrapped in ```latex fences should be cleaned before saving."""
        raw = f"```latex\n{VALID_LATEX}\n```"
        result, _ = self._patched_generate(tmp_path, raw, monkeypatch)
        content = (tmp_path / "output" / "TestCo_Resume.tex").read_text(encoding="utf-8")
        assert "```" not in content


# ═══════════════════════════════════════════════════════════════════════════════
# ClaudeCliProvider
# ═══════════════════════════════════════════════════════════════════════════════

class TestClaudeCliProviderMetadata:
    def test_provider_id(self):
        assert ClaudeCliProvider().provider_id == "claudecli"


class TestClaudeCliProviderGenerate:

    def _make_subprocess_result(self, returncode=0, stdout="", stderr=""):
        result = MagicMock()
        result.returncode = returncode
        result.stdout = stdout
        result.stderr = stderr
        return result

    @patch("core.providers.claude_cli.compile_latex")
    @patch("core.providers.claude_cli.subprocess.run")
    def test_writes_jd_to_job_description_txt(self, mock_run, mock_compile, tmp_path, monkeypatch):
        import core.providers.claude_cli as cli_module
        monkeypatch.setattr(cli_module, "BASE_DIR", str(tmp_path))
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "Acme_Resume.tex").write_text(VALID_LATEX, encoding="utf-8")
        (output_dir / "Acme_Resume.pdf").write_bytes(b"%PDF")

        mock_run.return_value = self._make_subprocess_result()

        req = _make_request(company_name="Acme", raw_job_description="JOB DESC CONTENT")
        ClaudeCliProvider().generate(req)

        jd_path = tmp_path / "job_description.txt"
        assert jd_path.exists()
        assert jd_path.read_text(encoding="utf-8") == "JOB DESC CONTENT"

    @patch("core.providers.claude_cli.compile_latex")
    @patch("core.providers.claude_cli.subprocess.run")
    def test_calls_claude_subprocess_with_company_name(self, mock_run, mock_compile, tmp_path, monkeypatch):
        import core.providers.claude_cli as cli_module
        monkeypatch.setattr(cli_module, "BASE_DIR", str(tmp_path))
        (tmp_path / "output").mkdir()
        (tmp_path / "output" / "Acme_Resume.tex").write_text(VALID_LATEX, encoding="utf-8")
        (tmp_path / "output" / "Acme_Resume.pdf").write_bytes(b"%PDF")

        mock_run.return_value = self._make_subprocess_result()

        ClaudeCliProvider().generate(_make_request(company_name="Acme"))

        args = mock_run.call_args[0][0]
        assert "claude" in args
        assert any("Acme" in str(a) for a in args)

    @patch("core.providers.claude_cli.subprocess.run")
    def test_raises_on_nonzero_subprocess_exit(self, mock_run, tmp_path, monkeypatch):
        import core.providers.claude_cli as cli_module
        monkeypatch.setattr(cli_module, "BASE_DIR", str(tmp_path))
        (tmp_path / "output").mkdir()

        mock_run.return_value = self._make_subprocess_result(returncode=1, stderr="error msg")

        with pytest.raises(RuntimeError, match="error msg"):
            ClaudeCliProvider().generate(_make_request())

    @patch("core.providers.claude_cli.subprocess.run")
    def test_raises_if_tex_file_not_produced(self, mock_run, tmp_path, monkeypatch):
        import core.providers.claude_cli as cli_module
        monkeypatch.setattr(cli_module, "BASE_DIR", str(tmp_path))
        (tmp_path / "output").mkdir()
        # No .tex file created

        mock_run.return_value = self._make_subprocess_result()

        with pytest.raises(RuntimeError, match="TeX file not found"):
            ClaudeCliProvider().generate(_make_request(company_name="Acme"))

    @patch("core.providers.claude_cli.compile_latex")
    @patch("core.providers.claude_cli.subprocess.run")
    def test_raises_if_pdf_not_produced_after_compile(self, mock_run, mock_compile, tmp_path, monkeypatch):
        import core.providers.claude_cli as cli_module
        monkeypatch.setattr(cli_module, "BASE_DIR", str(tmp_path))
        (tmp_path / "output").mkdir()
        (tmp_path / "output" / "Acme_Resume.tex").write_text(VALID_LATEX, encoding="utf-8")
        # No PDF created by mock_compile

        mock_run.return_value = self._make_subprocess_result()

        with pytest.raises(RuntimeError, match="PDF not found"):
            ClaudeCliProvider().generate(_make_request(company_name="Acme"))

    @patch("core.providers.claude_cli.compile_latex")
    @patch("core.providers.claude_cli.subprocess.run")
    def test_returns_correct_paths_on_success(self, mock_run, mock_compile, tmp_path, monkeypatch):
        import core.providers.claude_cli as cli_module
        monkeypatch.setattr(cli_module, "BASE_DIR", str(tmp_path))
        (tmp_path / "output").mkdir()
        (tmp_path / "output" / "Acme_Resume.tex").write_text(VALID_LATEX, encoding="utf-8")
        (tmp_path / "output" / "Acme_Resume.pdf").write_bytes(b"%PDF")

        mock_run.return_value = self._make_subprocess_result()

        result = ClaudeCliProvider().generate(_make_request(company_name="Acme"))

        assert result.tex_path.endswith("Acme_Resume.tex")
        assert result.pdf_path.endswith("Acme_Resume.pdf")

    @patch("core.providers.claude_cli.subprocess.run")
    def test_empty_stderr_on_nonzero_exit_uses_fallback_message(self, mock_run, tmp_path, monkeypatch):
        import core.providers.claude_cli as cli_module
        monkeypatch.setattr(cli_module, "BASE_DIR", str(tmp_path))
        (tmp_path / "output").mkdir()

        # returncode=1, stderr="" → should use fallback message
        mock_run.return_value = self._make_subprocess_result(returncode=1, stderr="")

        with pytest.raises(RuntimeError, match="claude -p exited with non-zero status"):
            ClaudeCliProvider().generate(_make_request())

    @patch("core.providers.claude_cli.compile_latex")
    @patch("core.providers.claude_cli.subprocess.run")
    def test_logs_subprocess_stdout(self, mock_run, mock_compile, tmp_path, monkeypatch):
        import core.providers.claude_cli as cli_module
        monkeypatch.setattr(cli_module, "BASE_DIR", str(tmp_path))
        (tmp_path / "output").mkdir()
        (tmp_path / "output" / "Acme_Resume.tex").write_text(VALID_LATEX, encoding="utf-8")
        (tmp_path / "output" / "Acme_Resume.pdf").write_bytes(b"%PDF")

        mock_run.return_value = self._make_subprocess_result(stdout="line1\nline2")

        logs = []
        ClaudeCliProvider().generate(_make_request(company_name="Acme", log=logs.append))

        assert "line1" in logs
        assert "line2" in logs
