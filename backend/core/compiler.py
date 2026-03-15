import os
import shutil
import subprocess
from dotenv import load_dotenv

load_dotenv()

PDFLATEX_FALLBACK_PATHS = [
    # Windows — MiKTeX (system-wide and per-user installs)
    r"C:\Program Files\MiKTeX\miktex\bin\x64\pdflatex.exe",
    r"C:\Program Files\MiKTeX 2.9\miktex\bin\x64\pdflatex.exe",
    os.path.expanduser(r"~\AppData\Local\Programs\MiKTeX\miktex\bin\x64\pdflatex.exe"),
    # macOS — MacTeX / Homebrew
    "/Library/TeX/texbin/pdflatex",
    "/usr/local/bin/pdflatex",
    "/opt/homebrew/bin/pdflatex",
    # Linux
    "/usr/bin/pdflatex",
    "/usr/local/bin/pdflatex",
]


def find_pdflatex():
    """Returns 'pdflatex' if on PATH, otherwise checks .env PDFLATEX_PATH or known install locations."""
    env_path = os.environ.get("PDFLATEX_PATH")
    if env_path and os.path.exists(env_path):
        return env_path
    if shutil.which("pdflatex"):
        return "pdflatex"
    for path in PDFLATEX_FALLBACK_PATHS:
        if os.path.exists(path):
            return path
    return "pdflatex"  # Will fail with a clear error


def compile_latex(tex_path, output_dir, log_callback=print):
    """Compiles the LaTeX file using pdflatex and cleans up aux files."""
    try:
        cmd = [find_pdflatex(), "-interaction=nonstopmode", f"-output-directory={output_dir}", tex_path]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if result.returncode != 0:
            log_callback("Warning: pdflatex returned a non-zero exit code. Check the log file for details.")
            log_callback("pdflatex output snippet:")
            log_callback("\n".join(result.stdout.splitlines()[-10:]))

    except FileNotFoundError:
        raise RuntimeError("pdflatex command not found. Please ensure a LaTeX distribution is installed and in your PATH.")

    # Cleanup auxiliary files
    base_name = os.path.splitext(os.path.basename(tex_path))[0]
    for ext in [".aux", ".log", ".out"]:
        file_to_remove = os.path.join(output_dir, f"{base_name}{ext}")
        if os.path.exists(file_to_remove):
            try:
                os.remove(file_to_remove)
            except OSError as e:
                log_callback(f"Failed to remove {file_to_remove}: {e}")
