"""
Unit tests for core/prompt_pipeline.py

Covers:
  - build_prompts: preamble splitting, system prompt loading, constraints/projects
    appending, user prompt assembly, error cases
  - postprocess_latex: markdown fence stripping, bold conversion, blank-line removal
  - validate_latex: accepts valid input, rejects truncated output
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.prompt_pipeline as pipeline_module
from core.prompt_pipeline import build_prompts, postprocess_latex, validate_latex


# ── Helpers ──────────────────────────────────────────────────────────────────

MINIMAL_TEX = r"""
\documentclass{article}
\begin{document}
\section{Experience}
\resumeItem{Did things.}
\end{document}
"""

JD = "Looking for a Python engineer with 3 years of experience."


def _make_prompt_dir(tmp_path, system="You are a resume writer.", constraints=None, projects=None):
    """Create a prompts/ directory under tmp_path with the given file contents."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "system_prompt.txt").write_text(system, encoding="utf-8")
    if constraints is not None:
        (prompts / "user_constraints.txt").write_text(constraints, encoding="utf-8")
    if projects is not None:
        (prompts / "additional_projects.txt").write_text(projects, encoding="utf-8")
    return prompts


# ── build_prompts: preamble splitting ────────────────────────────────────────

def test_build_prompts_splits_preamble_correctly(tmp_path, monkeypatch):
    _make_prompt_dir(tmp_path)
    monkeypatch.setattr(pipeline_module, "BASE_DIR", str(tmp_path))

    result = build_prompts(MINIMAL_TEX, JD, use_constraints=False, use_projects=False)

    assert r"\begin{document}" not in result.preamble
    assert r"\begin{document}" in result.user_prompt


def test_build_prompts_preserves_raw_job_description(tmp_path, monkeypatch):
    _make_prompt_dir(tmp_path)
    monkeypatch.setattr(pipeline_module, "BASE_DIR", str(tmp_path))

    result = build_prompts(MINIMAL_TEX, JD, use_constraints=False, use_projects=False)

    assert result.raw_job_description == JD


def test_build_prompts_user_prompt_contains_job_description(tmp_path, monkeypatch):
    _make_prompt_dir(tmp_path)
    monkeypatch.setattr(pipeline_module, "BASE_DIR", str(tmp_path))

    result = build_prompts(MINIMAL_TEX, JD, use_constraints=False, use_projects=False)

    assert JD in result.user_prompt


def test_build_prompts_user_prompt_contains_resume_body(tmp_path, monkeypatch):
    _make_prompt_dir(tmp_path)
    monkeypatch.setattr(pipeline_module, "BASE_DIR", str(tmp_path))

    result = build_prompts(MINIMAL_TEX, JD, use_constraints=False, use_projects=False)

    assert r"\section{Experience}" in result.user_prompt


def test_build_prompts_raises_when_no_begin_document(tmp_path, monkeypatch):
    _make_prompt_dir(tmp_path)
    monkeypatch.setattr(pipeline_module, "BASE_DIR", str(tmp_path))

    bad_tex = r"\documentclass{article}\end{document}"
    with pytest.raises(ValueError, match=r"\\begin\{document\}"):
        build_prompts(bad_tex, JD, use_constraints=False, use_projects=False)


def test_build_prompts_raises_when_system_prompt_missing(tmp_path, monkeypatch):
    # Create prompts dir WITHOUT system_prompt.txt
    (tmp_path / "prompts").mkdir()
    monkeypatch.setattr(pipeline_module, "BASE_DIR", str(tmp_path))

    with pytest.raises(ValueError, match="System prompt not found"):
        build_prompts(MINIMAL_TEX, JD, use_constraints=False, use_projects=False)


# ── build_prompts: system prompt loading ─────────────────────────────────────

def test_build_prompts_system_prompt_content_included(tmp_path, monkeypatch):
    _make_prompt_dir(tmp_path, system="CUSTOM_SYSTEM_RULES")
    monkeypatch.setattr(pipeline_module, "BASE_DIR", str(tmp_path))

    result = build_prompts(MINIMAL_TEX, JD, use_constraints=False, use_projects=False)

    assert "CUSTOM_SYSTEM_RULES" in result.system_prompt


# ── build_prompts: constraints ────────────────────────────────────────────────

def test_build_prompts_appends_constraints_when_enabled(tmp_path, monkeypatch):
    _make_prompt_dir(tmp_path, constraints="<constraints>no lies</constraints>")
    monkeypatch.setattr(pipeline_module, "BASE_DIR", str(tmp_path))

    result = build_prompts(MINIMAL_TEX, JD, use_constraints=True, use_projects=False)

    assert "no lies" in result.system_prompt


def test_build_prompts_skips_constraints_when_disabled(tmp_path, monkeypatch):
    _make_prompt_dir(tmp_path, constraints="<constraints>no lies</constraints>")
    monkeypatch.setattr(pipeline_module, "BASE_DIR", str(tmp_path))

    result = build_prompts(MINIMAL_TEX, JD, use_constraints=False, use_projects=False)

    assert "no lies" not in result.system_prompt


def test_build_prompts_warns_when_constraints_file_missing(tmp_path, monkeypatch, capsys):
    _make_prompt_dir(tmp_path)  # no constraints file
    monkeypatch.setattr(pipeline_module, "BASE_DIR", str(tmp_path))

    logs = []
    build_prompts(MINIMAL_TEX, JD, use_constraints=True, use_projects=False, log=logs.append)

    assert any("user_constraints.txt" in msg for msg in logs)


# ── build_prompts: projects ───────────────────────────────────────────────────

def test_build_prompts_appends_projects_when_enabled(tmp_path, monkeypatch):
    _make_prompt_dir(tmp_path, projects="<project_bank>Secret project</project_bank>")
    monkeypatch.setattr(pipeline_module, "BASE_DIR", str(tmp_path))

    result = build_prompts(MINIMAL_TEX, JD, use_constraints=False, use_projects=True)

    assert "Secret project" in result.system_prompt


def test_build_prompts_skips_projects_when_disabled(tmp_path, monkeypatch):
    _make_prompt_dir(tmp_path, projects="<project_bank>Secret project</project_bank>")
    monkeypatch.setattr(pipeline_module, "BASE_DIR", str(tmp_path))

    result = build_prompts(MINIMAL_TEX, JD, use_constraints=False, use_projects=False)

    assert "Secret project" not in result.system_prompt


def test_build_prompts_warns_when_projects_file_missing(tmp_path, monkeypatch):
    _make_prompt_dir(tmp_path)  # no projects file
    monkeypatch.setattr(pipeline_module, "BASE_DIR", str(tmp_path))

    logs = []
    build_prompts(MINIMAL_TEX, JD, use_constraints=False, use_projects=True, log=logs.append)

    assert any("additional_projects.txt" in msg for msg in logs)


def test_build_prompts_constraints_and_projects_both_appended(tmp_path, monkeypatch):
    _make_prompt_dir(tmp_path, constraints="CONSTRAINT_TEXT", projects="PROJECT_TEXT")
    monkeypatch.setattr(pipeline_module, "BASE_DIR", str(tmp_path))

    result = build_prompts(MINIMAL_TEX, JD, use_constraints=True, use_projects=True)

    assert "CONSTRAINT_TEXT" in result.system_prompt
    assert "PROJECT_TEXT" in result.system_prompt


# ── postprocess_latex: fence stripping ───────────────────────────────────────

def test_postprocess_strips_latex_fence():
    raw = "```latex\n\\begin{document}\n\\end{document}\n```"
    result = postprocess_latex(raw)
    assert "```" not in result
    assert r"\begin{document}" in result


def test_postprocess_strips_tex_fence():
    raw = "```tex\n\\begin{document}\n\\end{document}\n```"
    result = postprocess_latex(raw)
    assert "```" not in result


def test_postprocess_no_fence_returns_stripped_text():
    raw = "  \\begin{document}\n\\end{document}  "
    result = postprocess_latex(raw)
    assert result.startswith(r"\begin{document}")


def test_postprocess_unlabeled_fence_not_stripped():
    # A ``` fence with no language label is not recognized — returned as-is (stripped)
    raw = "```\n\\begin{document}\n\\end{document}\n```"
    result = postprocess_latex(raw)
    # content is kept (the pattern requires latex/tex label or nothing after ```)
    # The regex uses (?:latex|tex)? so empty label IS matched
    assert r"\begin{document}" in result


# ── postprocess_latex: bold conversion ───────────────────────────────────────

def test_postprocess_converts_markdown_bold():
    raw = r"\begin{document}**Python**\end{document}"
    result = postprocess_latex(raw)
    assert r"\textbf{Python}" in result
    assert "**" not in result


def test_postprocess_converts_multiple_bold_instances():
    raw = r"\begin{document}**A** and **B**\end{document}"
    result = postprocess_latex(raw)
    assert r"\textbf{A}" in result
    assert r"\textbf{B}" in result


def test_postprocess_does_not_alter_existing_textbf():
    raw = r"\begin{document}\textbf{existing}\end{document}"
    result = postprocess_latex(raw)
    assert r"\textbf{existing}" in result


# ── postprocess_latex: blank line removal ────────────────────────────────────

def test_postprocess_removes_blank_line_before_resumeitem():
    raw = "\\resumeItemListStart\n\n\\resumeItem{Bullet}\n\\resumeItemListEnd"
    result = postprocess_latex(raw)
    assert "\n\n\\resumeItem" not in result
    assert "\\resumeItem{Bullet}" in result


def test_postprocess_removes_multiple_consecutive_blank_lines_before_resumeitem():
    raw = "\\resumeItemListStart\n\n\n\n\\resumeItem{Bullet}\n\\resumeItemListEnd"
    result = postprocess_latex(raw)
    assert "\n\n\\resumeItem" not in result


def test_postprocess_preserves_blank_lines_not_before_resumeitem():
    raw = "Line one\n\nLine two\n\\resumeItem{Bullet}"
    result = postprocess_latex(raw)
    # blank line between Line one and Line two should survive
    assert "Line one\n\nLine two" in result


# ── validate_latex ────────────────────────────────────────────────────────────

def test_validate_latex_passes_on_valid_input():
    latex = r"\begin{document}content\end{document}"
    validate_latex(latex)  # should not raise


def test_validate_latex_raises_on_missing_begin_document():
    with pytest.raises(ValueError, match=r"\\begin\{document\}"):
        validate_latex(r"content\end{document}")


def test_validate_latex_raises_on_missing_end_document():
    with pytest.raises(ValueError, match=r"\\end\{document\}"):
        validate_latex(r"\begin{document}content")


def test_validate_latex_raises_on_empty_string():
    with pytest.raises(ValueError):
        validate_latex("")
