"""Unit tests for the LaTeX resume parser."""

import os
import sys

import pytest

# Add backend to path so we can import core.tex_parser
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.tex_parser import parse_resume_tex, clean_latex


# ── Load master_resume.tex once for all tests ────────────────────────────────
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MASTER_RESUME_PATH = os.path.join(REPO_ROOT, "resumes", "master_resume.tex")

with open(MASTER_RESUME_PATH, "r", encoding="utf-8") as _f:
    MASTER_TEX = _f.read()

RESULT = parse_resume_tex(MASTER_TEX)


# ── Structure tests ──────────────────────────────────────────────────────────

def test_output_has_experience_and_projects_keys():
    assert "experience" in RESULT
    assert "projects" in RESULT


def test_experience_is_list():
    assert isinstance(RESULT["experience"], list)


def test_projects_is_list():
    assert isinstance(RESULT["projects"], list)


def test_experience_entry_structure():
    for entry in RESULT["experience"]:
        assert "company" in entry
        assert "tech_stack" in entry
        assert "dates" in entry
        assert "role" in entry
        assert "location" in entry
        assert "bullets" in entry


def test_project_entry_structure():
    for entry in RESULT["projects"]:
        assert "name" in entry
        assert "tech_stack" in entry
        assert "bullets" in entry


def test_bullets_are_lists_of_strings():
    for entry in RESULT["experience"]:
        assert isinstance(entry["bullets"], list)
        for b in entry["bullets"]:
            assert isinstance(b, str)
    for entry in RESULT["projects"]:
        assert isinstance(entry["bullets"], list)
        for b in entry["bullets"]:
            assert isinstance(b, str)


# ── Count tests ──────────────────────────────────────────────────────────────

def test_experience_count():
    assert len(RESULT["experience"]) == 3


def test_project_count():
    assert len(RESULT["projects"]) == 2


def test_experience_bullet_counts():
    assert len(RESULT["experience"][0]["bullets"]) == 4  # AWS
    assert len(RESULT["experience"][1]["bullets"]) == 3  # Acute
    assert len(RESULT["experience"][2]["bullets"]) == 3  # WPServiceDesk


# ── Value tests (spot-check) ─────────────────────────────────────────────────

def test_first_experience_company():
    assert RESULT["experience"][0]["company"] == "Amazon Web Services (AWS)"


def test_first_experience_role():
    assert RESULT["experience"][0]["role"] == "Software Development Engineer Intern"


def test_first_experience_location():
    assert RESULT["experience"][0]["location"] == "East Palo Alto, CA, USA"


def test_first_experience_dates():
    assert RESULT["experience"][0]["dates"] == "May 2025 - Aug 2025"


def test_first_experience_tech_stack():
    tech = RESULT["experience"][0]["tech_stack"]
    assert "Bedrock" in tech
    assert "Lambda" in tech
    assert "CDK" in tech


def test_second_experience_company():
    assert RESULT["experience"][1]["company"] == "Acute Informatics Pvt. Ltd."


def test_first_project_name():
    assert RESULT["projects"][0]["name"] == "Distributed File System"


def test_second_project_name():
    assert RESULT["projects"][1]["name"] == "Go HTTP Server"


def test_first_project_tech_stack():
    tech = RESULT["projects"][0]["tech_stack"]
    assert "C++" in tech
    assert "P2P" in tech
    assert "AES Encryption" in tech


# ── Clean LaTeX tests ────────────────────────────────────────────────────────

def test_no_latex_commands_in_bullets():
    for entry in RESULT["experience"] + RESULT["projects"]:
        for b in entry["bullets"]:
            assert r"\textbf" not in b, f"Found \\textbf in: {b}"
            assert r"\footnotesize" not in b, f"Found \\footnotesize in: {b}"
            assert r"\resumeItem" not in b, f"Found \\resumeItem in: {b}"


def test_no_unescaped_latex_chars():
    for entry in RESULT["experience"] + RESULT["projects"]:
        for b in entry["bullets"]:
            assert r"\&" not in b, f"Found \\& in: {b}"
            assert r"\%" not in b, f"Found \\% in: {b}"
            assert r"\$" not in b, f"Found \\$ in: {b}"
            assert r"\#" not in b, f"Found \\# in: {b}"
            assert r"\_" not in b, f"Found \\_ in: {b}"


def test_bullet_text_no_braces():
    for entry in RESULT["experience"] + RESULT["projects"]:
        for b in entry["bullets"]:
            assert "{" not in b, f"Found stray {{ in: {b}"
            assert "}" not in b, f"Found stray }} in: {b}"


# ── Edge case tests ──────────────────────────────────────────────────────────

def test_empty_input():
    result = parse_resume_tex("")
    assert result == {"experience": [], "projects": []}


def test_no_experience_section():
    tex = r"""
\section{Projects}
    \resumeSubHeadingListStart
      \resumeProjectHeading{\textbf{MyProject \textbar{} \footnotesize{Python}}}{}
      \resumeItemListStart
        \resumeItem{Did something cool.}
      \resumeItemListEnd
    \resumeSubHeadingListEnd
"""
    result = parse_resume_tex(tex)
    assert result["experience"] == []
    assert len(result["projects"]) == 1


def test_no_projects_section():
    tex = r"""
\section{Experience}
  \resumeSubHeadingListStart
    \resumeSubheading{ACME Corp}{2024}{Engineer}{NY}
    \resumeItemListStart
      \resumeItem{Built things.}
    \resumeItemListEnd
  \resumeSubHeadingListEnd
\section{Education}
"""
    result = parse_resume_tex(tex)
    assert len(result["experience"]) == 1
    assert result["projects"] == []
