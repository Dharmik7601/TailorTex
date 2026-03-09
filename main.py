import argparse
import os
import re
import sys
import subprocess
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

def main():
    parser = argparse.ArgumentParser(description="TailorTex - AI tailored LaTeX resumes")
    parser.add_argument("--jd", required=True, help="Path to job description text file")
    parser.add_argument("--output", required=True, help="Company Name for the output files")
    parser.add_argument("--prompt", default="prompts/system_prompt.txt", help="Path to the system prompt file")
    parser.add_argument("--constraints", action="store_true", help="Include user_constraints.txt in the prompt")
    parser.add_argument("--projects", action="store_true", help="Include additional_projects.txt in the prompt")
    
    args = parser.parse_args()
    
    jd_path = args.jd
    company_name = args.output
    prompt_path = args.prompt
    
    # 1. Load Files
    if not os.path.exists(jd_path):
        print(f"Error: Job description file not found at {jd_path}")
        sys.exit(1)
        
    master_resume_path = "master_resume.tex"
    if not os.path.exists(master_resume_path):
        print(f"Error: {master_resume_path} not found in the current directory.")
        sys.exit(1)
        
    with open(jd_path, 'r', encoding='utf-8') as f:
        job_description = f.read()
        
    with open(master_resume_path, 'r', encoding='utf-8') as f:
        master_resume_full = f.read()

    # Split preamble to save tokens
    delimiter = r"\begin{document}"
    if delimiter in master_resume_full:
        parts = master_resume_full.split(delimiter, 1)
        preamble = parts[0]
        resume_body = delimiter + parts[1]
    else:
        print(f"Error: {delimiter} not found in {master_resume_path}")
        sys.exit(1)

    if not os.path.exists(prompt_path):
        print(f"Error: Prompt file not found at {prompt_path}")
        sys.exit(1)
        
    with open(prompt_path, 'r', encoding='utf-8') as f:
        system_prompt = f.read()

    # Conditionally append user constraints
    if args.constraints:
        constraints_path = "prompts/user_constraints.txt"
        if os.path.exists(constraints_path):
            with open(constraints_path, 'r', encoding='utf-8') as f:
                constraints_text = f.read()
            system_prompt += f"\n\nUSER REQUIREMENTS & CONSTRAINTS\n{constraints_text}\n"
        else:
            print(f"Warning: --constraints flag was used, but {constraints_path} was not found.")

    # Conditionally append extra projects
    if args.projects:
        projects_path = "prompts/additional_projects.txt"
        if os.path.exists(projects_path):
            with open(projects_path, 'r', encoding='utf-8') as f:
                projects_text = f.read()
            system_prompt += f"\n\nADDITIONAL USER PROJECTS\nYou may use these projects directly or modify them to align with the job description:\n{projects_text}\n"
        else:
            print(f"Warning: --projects flag was used, but {projects_path} was not found.")

    # Phase 2: Prompt Engineering
    user_prompt = f"""Job Description:\n{job_description}\n\n---\nMaster Resume Body (LaTeX):\n{resume_body}\n"""
    
    print("Sending prompt to Gemini API...")
    
    # Phase 3: The API Call & Data Parsing
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable not set.")
        # sys.exit(1) # Un-comment in prod. For local AI studio/vertex testing we might rely on default auth
        # Fallback to default auth if key is missing
        client = genai.Client()
    else:
        client = genai.Client(api_key=api_key)

    models_to_try = ['gemini-3-flash-preview', 'gemini-2.5-flash']
    llm_output = None
    last_error = None
    
    for model_name in models_to_try:
        try:
            print(f"Trying model: {model_name}...")
            response = client.models.generate_content(
                model=model_name,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.2, # Low temperature for more deterministic structural adherence
                )
            )
            llm_output = response.text
            print(f"Successfully generated content using {model_name}.")
            break # Break out of the loop immediately if successful
        except Exception as e:
            last_error = e
            print(f"Model {model_name} failed: {e}. Attempting fallback...")
            
    if llm_output is None:
        print(f"Error: All Gemini models failed. Last error: {last_error}")
        sys.exit(1)
        
    # Regex Extraction
    clean_latex = extract_latex(llm_output)
    
    # Sanity Check
    if r"\begin{document}" not in clean_latex or r"\end{document}" not in clean_latex:
        print("Error: LLM output is missing \\begin{document} or \\end{document}. The response might be truncated.")
        sys.exit(1)
        
    # Save the file
    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)
    
    output_tex_filename = f"{company_name}_Resume.tex"
    output_tex_path = os.path.join(output_dir, output_tex_filename)
    
    with open(output_tex_path, 'w', encoding='utf-8') as f:
        f.write(preamble + "\n" + clean_latex)
        
    print(f"Saved generated LaTeX to {output_tex_path}")
    
    # Phase 4: Compilation via Subprocess
    print("Compiling LaTeX to PDF...")
    compile_latex(output_tex_path, output_dir)
    print("Done!")

def extract_latex(text):
    """Strips away markdown wrappers if the LLM includes them."""
    # Look for ```latex ... ``` or just ``` ... ```
    pattern = r"```(?:latex|tex)?\n(.*?)```"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text.strip()

def compile_latex(tex_path, output_dir):
    """Compiles the LaTeX file using pdflatex and cleans up aux files."""
    try:
        # Run pdflatex. Needs to be executed twice sometimes for references, but once should be fine for a basic resume.
        cmd = ["pdflatex", "-interaction=nonstopmode", f"-output-directory={output_dir}", tex_path]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        if result.returncode != 0:
            print("Warning: pdflatex returned a non-zero exit code. Check the log file for details.")
            print("pdflatex output snippet:")
            print("\n".join(result.stdout.splitlines()[-10:]))
            
    except FileNotFoundError:
        print("Error: pdflatex command not found. Please ensure a LaTeX distribution is installed and in your PATH.")
        sys.exit(1)
        
    # Cleanup auxiliary files
    base_name = os.path.splitext(os.path.basename(tex_path))[0]
    extensions_to_remove = ['.aux', '.log', '.out']
    
    for ext in extensions_to_remove:
        file_to_remove = os.path.join(output_dir, f"{base_name}{ext}")
        if os.path.exists(file_to_remove):
            try:
                os.remove(file_to_remove)
            except OSError as e:
                print(f"Failed to remove {file_to_remove}: {e}")

if __name__ == "__main__":
    main()
