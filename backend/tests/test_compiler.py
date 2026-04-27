"""
Unit tests for core/compiler.py

Covers:
  - find_pdflatex: PDFLATEX_PATH env var, shutil.which hit, fallback paths, default
  - compile_latex: non-zero exit code, FileNotFoundError, aux file cleanup, OSError during cleanup
"""

import os
import sys

import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.compiler import find_pdflatex, compile_latex


# ═══════════════════════════════════════════════════════════════════════════════
# find_pdflatex
# ═══════════════════════════════════════════════════════════════════════════════

def test_find_pdflatex_uses_env_path_when_set_and_exists(tmp_path):
    fake_binary = tmp_path / "pdflatex.exe"
    fake_binary.write_bytes(b"")

    with patch.dict(os.environ, {"PDFLATEX_PATH": str(fake_binary)}):
        result = find_pdflatex()

    assert result == str(fake_binary)


def test_find_pdflatex_skips_env_path_when_not_on_disk(tmp_path):
    nonexistent = str(tmp_path / "does_not_exist" / "pdflatex.exe")

    with patch.dict(os.environ, {"PDFLATEX_PATH": nonexistent}), \
         patch("core.compiler.shutil.which", return_value="pdflatex"):
        result = find_pdflatex()

    # Falls through to shutil.which since env path doesn't exist
    assert result == "pdflatex"



def test_find_pdflatex_returns_default_when_nothing_found():
    env_without_path = {k: v for k, v in os.environ.items() if k != "PDFLATEX_PATH"}
    with patch.dict(os.environ, env_without_path, clear=True), \
         patch("core.compiler.shutil.which", return_value=None), \
         patch("os.path.exists", return_value=False):
        result = find_pdflatex()

    assert result == "pdflatex"


# ═══════════════════════════════════════════════════════════════════════════════
# compile_latex
# ═══════════════════════════════════════════════════════════════════════════════

def test_compile_latex_nonzero_exit_logs_warning_not_raises(tmp_path):
    tex = tmp_path / "test.tex"
    tex.write_text(r"\begin{document}\end{document}")

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = "error line 1\nerror line 2"

    logs = []
    with patch("core.compiler.subprocess.run", return_value=mock_result), \
         patch("core.compiler.find_pdflatex", return_value="pdflatex"):
        compile_latex(str(tex), str(tmp_path), log_callback=logs.append)

    # Must log a warning but not raise
    assert any("Warning" in msg or "non-zero" in msg for msg in logs)


def test_compile_latex_file_not_found_raises_runtime_error(tmp_path):
    tex = tmp_path / "test.tex"
    tex.write_text(r"\begin{document}\end{document}")

    with patch("core.compiler.subprocess.run", side_effect=FileNotFoundError), \
         patch("core.compiler.find_pdflatex", return_value="pdflatex"):
        with pytest.raises(RuntimeError, match="pdflatex command not found"):
            compile_latex(str(tex), str(tmp_path))


def test_compile_latex_cleans_up_aux_files(tmp_path):
    tex = tmp_path / "MyResume.tex"
    tex.write_text(r"\begin{document}\end{document}")

    # Create the aux files that pdflatex would normally produce
    (tmp_path / "MyResume.aux").write_text("aux content")
    (tmp_path / "MyResume.log").write_text("log content")
    (tmp_path / "MyResume.out").write_text("out content")

    mock_result = MagicMock()
    mock_result.returncode = 0

    with patch("core.compiler.subprocess.run", return_value=mock_result), \
         patch("core.compiler.find_pdflatex", return_value="pdflatex"):
        compile_latex(str(tex), str(tmp_path))

    assert not (tmp_path / "MyResume.aux").exists()
    assert not (tmp_path / "MyResume.log").exists()
    assert not (tmp_path / "MyResume.out").exists()


def test_compile_latex_cleanup_oserror_logs_not_raises(tmp_path):
    tex = tmp_path / "ErrorClean.tex"
    tex.write_text(r"\begin{document}\end{document}")
    (tmp_path / "ErrorClean.aux").write_text("aux")

    mock_result = MagicMock()
    mock_result.returncode = 0

    logs = []
    with patch("core.compiler.subprocess.run", return_value=mock_result), \
         patch("core.compiler.find_pdflatex", return_value="pdflatex"), \
         patch("os.remove", side_effect=OSError("permission denied")):
        compile_latex(str(tex), str(tmp_path), log_callback=logs.append)

    # Must log the OSError but not raise
    assert any("Failed to remove" in msg or "permission denied" in msg for msg in logs)
