# TailorTex 🎯📄

TailorTex is a CLI-based Python tool designed to automatically rewrite and tailor a static LaTeX resume to perfectly match a specific job description. It uses Google's Gemini LLMs to reframe your experience, inject exact keywords, and intelligently rewrite bullet points, all while strictly preserving your precise LaTeX formatting and ensuring the output fits on exactly one page.

## Features

- **Automated Tailoring**: Rewrites your experience and project descriptions to aggressively align with the provided Job Description.
- **Strict Formatting Guardrails**: Only modifies whitelisted sections. It will never break your LaTeX structure, change your company names/job titles, or mess with vertical spacing.
- **One-Page Guarantee**: Uses strict token constraints to guarantee a precise 1-page output.
- **Local Compilation**: Automatically compiles the generated `.tex` file into a `.pdf` using your local installation of `pdflatex`, then cleans up the garbage `.aux` and `.log` files.
- **Optional Prompt Injections**: Inject custom requirements (like "Don't change X job") or provide a bank of extra side-projects for the AI to pick from via simple CLI flags.

## Requirements

1. **Python 3.x**
2. **Google GenAI SDK**: `pip install -r requirements.txt` (Installs `google-genai` and `python-dotenv`)
3. **LaTeX Distribution**: You MUST have a working installation of LaTeX on your system (e.g., [MiKTeX](https://miktex.org/) on Windows or TeX Live on Mac/Linux) so that the `pdflatex` command is available in your system PATH.
4. **Gemini API Key**: Create a `.env` file in the root directory and add:
   `GEMINI_API_KEY=your_api_key_here`

## Project Structure

```text
TailorTex/
├── main.py                    # The core generation script
├── compile.py                 # Standalone script for manual PDF compilation
├── Makefile                   # Make commands for easy execution
├── master_resume.tex          # YOUR base resume template (edit this!)
├── output/                    # Generated PDFs and TeX files are saved here
└── prompts/                   # Contains AI instructions:
    ├── system_prompt.txt      # The core rules the AI must follow
    ├── user_constraints.txt   # (Optional) Hard rules for specific generations
    └── additional_projects.txt# (Optional) Project bank the AI can pull from
```

## How to Use

### Step 1: Prepare your inputs
1. Place your base resume in `master_resume.tex`. If you change the structure of this template, ensure you update the instructions in `prompts/system_prompt.txt` as they are specifically designed to follow it.
2. Save the target Job Description inside `job_description.txt`.

### Step 2: Run the Pipeline (via Makefile)
The easiest way to use the program is via the `Makefile`. 

Open your terminal in the TailorTex directory and run:
```bash
make run NAME=TargetCompany
```
*This will generate `TargetCompany_Resume.tex`, compile it to a `.pdf`, save it in the `output/` folder, and automatically open it for you.*

**Using Enhancements (Constraints & Projects):**
If you want to feed the AI your `prompts/user_constraints.txt` and `prompts/additional_projects.txt` files, simply set their flags to `true`:
```bash
make run JD_FILE=job.txt NAME=TargetCompany CONSTRAINTS=true PROJECTS=true
```

## Customizing the Prompts

If the AI is modifying things you don't want it to, or not being aggressive enough, you can tweak the files in the `prompts/` directory:

- **`system_prompt.txt`**: This is the engine room. If you change your LaTeX template structure, you MUST update the "Content Modification Rules (Whitelist)" section in this file, or the AI will break your formatting.
- **`user_constraints.txt`**: Use this to define one-off rules per application. 
- **`additional_projects.txt`**: Paste projects you have done into this file. The AI will read through them and selectively swap them into your resume if it thinks they fit the job description better than the ones currently in `master_resume.tex`.