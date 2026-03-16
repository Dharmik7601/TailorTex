import argparse
import subprocess
import os
import shutil

def compile_latex(tex_file, output_name, output_dir):
    """Compiles a LaTeX file into a PDF and cleans up auxiliary files."""
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Read the content of the input tex file
    try:
        with open(tex_file, 'r', encoding='utf-8') as f:
            tex_content = f.read()
    except Exception as e:
        print(f"Error reading {tex_file}: {e}")
        return

    # Define the output file paths
    output_tex_path = os.path.join(output_dir, f"{output_name}_Resume.tex")
    output_pdf_path = os.path.join(output_dir, f"{output_name}_Resume.pdf")
    
    print(f"Copying {tex_file} content to {output_tex_path}...")
    try:
        with open(output_tex_path, 'w', encoding='utf-8') as f:
            f.write(tex_content)
    except Exception as e:
        print(f"Error writing to {output_tex_path}: {e}")
        return
        
    print("Compiling LaTeX to PDF...")
    command = [
        "pdflatex",
        "-interaction=nonstopmode",
        f"-output-directory={output_dir}",
        output_tex_path
    ]

    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        # Clean up auxiliary files
        for ext in ['.aux', '.log', '.out']:
            aux_file = os.path.join(output_dir, f"{output_name}_Resume{ext}")
            if os.path.exists(aux_file):
                os.remove(aux_file)

        if result.returncode != 0:
            print(f"Warning: pdflatex returned a non-zero exit code. Check the log file for details.")
            if os.path.exists(output_pdf_path):
                 print(f"PDF was still generated despite warnings: {output_pdf_path}")
            return
                
        print(f"Successfully compiled {output_pdf_path}!")
        
    except Exception as e:
        print(f"Error running pdflatex: {e}. Is MiKTeX or TeX Live installed and in your PATH?")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compile a local LaTeX file into a final resume PDF.")
    parser.add_argument("--tex", default="temp_resume.tex", help="Path to the source TeX file to compile.")
    parser.add_argument("--prefix", required=True, help="Prefix name for the output PDF (e.g. 'Dharmik'). Extends to '{prefix}_Resume.pdf'")
    parser.add_argument("--output", default="output", help="Directory to save the compiled PDF.")
    
    args = parser.parse_args()
    compile_latex(args.tex, args.prefix, args.output)