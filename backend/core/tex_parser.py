"""Parse a generated LaTeX resume into structured Experience and Projects data."""

import re


def extract_brace_arg(text: str, pos: int) -> tuple[str, int]:
    """From an opening '{' at *pos*, return (content, end_pos) via brace-depth tracking."""
    if pos >= len(text) or text[pos] != "{":
        return ("", pos)
    depth = 1
    i = pos + 1
    while i < len(text) and depth > 0:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return (text[pos + 1 : i - 1], i)


def clean_latex(text: str) -> str:
    """Strip LaTeX formatting commands to produce clipboard-friendly plain text."""
    # Unwrap commands with a single brace arg: \textbf{X} -> X, etc.
    changed = True
    while changed:
        changed = False
        for cmd in (r"\\textbf", r"\\footnotesize", r"\\textit", r"\\underline", r"\\emph", r"\\small"):
            pattern = cmd + r"\{"
            match = re.search(pattern, text)
            if match:
                content, end = extract_brace_arg(text, match.end() - 1)
                text = text[: match.start()] + content + text[end:]
                changed = True

    # Replace known LaTeX symbols
    text = text.replace(r"\textbar{}", "|")
    text = text.replace(r"\textbar", "|")
    text = text.replace(r"\&", "&")
    text = text.replace(r"\%", "%")
    text = text.replace(r"\$", "$")
    text = text.replace(r"\#", "#")
    text = text.replace(r"\_", "_")
    text = text.replace("$|$", "|")

    # Remove \vspace{...}
    text = re.sub(r"\\vspace\{[^}]*\}", "", text)
    # Remove stray \\
    text = text.replace("\\\\", "")
    # Remove \href{url}{text} -> text
    text = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", text)

    # Strip/collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = text.strip()
    return text


def _extract_section(tex: str, section_name: str) -> str:
    """Extract content between \\section{name} and the next \\section{ or end."""
    pattern = re.compile(r"\\section\{" + re.escape(section_name) + r"\}", re.IGNORECASE)
    m = pattern.search(tex)
    if not m:
        return ""
    start = m.end()
    next_section = re.search(r"\\section\{", tex[start:])
    if next_section:
        return tex[start : start + next_section.start()]
    return tex[start:]


def _find_all_command(text: str, command: str) -> list[int]:
    """Find all positions of \\command in text."""
    positions = []
    pattern = "\\" + command
    idx = 0
    while True:
        pos = text.find(pattern, idx)
        if pos == -1:
            break
        positions.append(pos)
        idx = pos + len(pattern)
    return positions


def _extract_args(text: str, pos: int, count: int) -> tuple[list[str], int]:
    """Extract *count* consecutive brace arguments starting from *pos*."""
    args = []
    i = pos
    for _ in range(count):
        # Skip whitespace/newlines to find next '{'
        while i < len(text) and text[i] != "{":
            i += 1
        if i >= len(text):
            break
        content, i = extract_brace_arg(text, i)
        args.append(content)
    return args, i


def _extract_bullets(text: str) -> list[str]:
    """Extract all \\resumeItem{...} bullet texts from a block, cleaned."""
    bullets = []
    # Use regex to match \resumeItem{ exactly (not \resumeItemListStart etc.)
    for m in re.finditer(r"\\resumeItem\{", text):
        brace_start = m.end() - 1  # position of the '{'
        content, _ = extract_brace_arg(text, brace_start)
        cleaned = clean_latex(content)
        if cleaned:
            bullets.append(cleaned)
    return bullets


def _parse_experience(section_text: str) -> list[dict]:
    """Parse experience entries from the Experience section text."""
    entries = []
    positions = _find_all_command(section_text, "resumeSubheading")

    for i, pos in enumerate(positions):
        after_cmd = pos + len("\\resumeSubheading")
        args, end_pos = _extract_args(section_text, after_cmd, 4)
        if len(args) < 4:
            continue

        raw_line1 = args[0]  # "Company \textbar{} \footnotesize{tech}"
        dates = clean_latex(args[1])
        role = clean_latex(args[2])
        location = clean_latex(args[3])

        # Split company line on \textbar{} or | to get company + tech_stack
        if r"\textbar{}" in raw_line1:
            parts = raw_line1.split(r"\textbar{}", 1)
        elif r"\textbar" in raw_line1:
            parts = raw_line1.split(r"\textbar", 1)
        else:
            parts = [raw_line1]

        company = clean_latex(parts[0])
        tech_stack = clean_latex(parts[1]) if len(parts) > 1 else ""

        # Find bullets between this entry and the next
        if i + 1 < len(positions):
            entry_block = section_text[pos:positions[i + 1]]
        else:
            entry_block = section_text[pos:]

        bullets = _extract_bullets(entry_block)

        entries.append({
            "company": company,
            "tech_stack": tech_stack,
            "dates": dates,
            "role": role,
            "location": location,
            "bullets": bullets,
        })

    return entries


def _parse_projects(section_text: str) -> list[dict]:
    """Parse project entries from the Projects section text."""
    entries = []
    positions = _find_all_command(section_text, "resumeProjectHeading")

    for i, pos in enumerate(positions):
        after_cmd = pos + len("\\resumeProjectHeading")
        args, end_pos = _extract_args(section_text, after_cmd, 2)
        if len(args) < 1:
            continue

        raw_line1 = args[0]  # "\textbf{ProjectName \textbar{} \footnotesize{tech}}"

        # Split on \textbar{} or | first (before cleaning, to separate name from tech)
        cleaned_line = clean_latex(raw_line1)
        if "|" in cleaned_line:
            parts = cleaned_line.split("|", 1)
            name = parts[0].strip()
            tech_stack = parts[1].strip()
        else:
            name = cleaned_line
            tech_stack = ""

        # Find bullets between this entry and the next
        if i + 1 < len(positions):
            entry_block = section_text[pos:positions[i + 1]]
        else:
            entry_block = section_text[pos:]

        bullets = _extract_bullets(entry_block)

        entries.append({
            "name": name,
            "tech_stack": tech_stack,
            "bullets": bullets,
        })

    return entries


def parse_resume_tex(tex_content: str) -> dict:
    """Parse a LaTeX resume and return structured experience and projects data."""
    if not tex_content or not tex_content.strip():
        return {"experience": [], "projects": []}

    experience_section = _extract_section(tex_content, "Experience")
    projects_section = _extract_section(tex_content, "Projects")

    return {
        "experience": _parse_experience(experience_section),
        "projects": _parse_projects(projects_section),
    }
