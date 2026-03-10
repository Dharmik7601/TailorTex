import argparse
import os
import sys
from dotenv import load_dotenv
from core.generator import generate_resume

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="TailorTex - AI tailored LaTeX resumes")
    parser.add_argument("--jd", required=True, help="Path to job description text file")
    parser.add_argument("--output", required=True, help="Company Name for the output files")
    parser.add_argument("--constraints", action="store_true", help="Include user_constraints.txt in the prompt")
    parser.add_argument("--projects", action="store_true", help="Include additional_projects.txt in the prompt")

    args = parser.parse_args()

    if not os.path.exists(args.jd):
        print(f"Error: Job description file not found at {args.jd}")
        sys.exit(1)

    master_resume_path = "master_resume.tex"
    if not os.path.exists(master_resume_path):
        print(f"Error: {master_resume_path} not found in the current directory.")
        sys.exit(1)

    with open(args.jd, "r", encoding="utf-8") as f:
        job_description = f.read()

    with open(master_resume_path, "r", encoding="utf-8") as f:
        master_resume_tex = f.read()

    try:
        generate_resume(
            master_resume_tex=master_resume_tex,
            job_description=job_description,
            company_name=args.output,
            use_constraints=args.constraints,
            use_projects=args.projects,
            log_callback=print,
        )
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
