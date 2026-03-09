import os
import shutil
import re
from datetime import date
from dotenv import load_dotenv

def get_ordinal(n):
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return str(n) + {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')

def main():
    # Load environment variables from .env file
    load_dotenv()
    
    # Get the target backup location from the environment variable
    backup_base_dir = os.environ.get("BACKUP_LOCATION")
    
    if not backup_base_dir:
        print("Error: BACKUP_LOCATION environment variable is not set in your .env file.")
        print("Please add a line like: BACKUP_LOCATION=C:\\Path\\To\\Your\\Backup\\Folder")
        return

    # Ensure the base backup location exists
    if not os.path.exists(backup_base_dir):
        print(f"Error: The target backup location '{backup_base_dir}' does not exist.")
        return

    source_dir = "output"
    if not os.path.exists(source_dir):
        print(f"Error: The source directory '{source_dir}' does not exist.")
        return

    files_copied = 0
    today = date.today()
    # Format date string (e.g., '9thMarch2026')
    date_str = get_ordinal(today.day) + today.strftime("%B%Y")

    # Iterate through files in the output directory
    for filename in os.listdir(source_dir):
        if filename.endswith(".pdf") or filename.endswith(".tex"):
            file_path = os.path.join(source_dir, filename)
            
            # The name before the first '-' or '_' is the company name
            first_dash = filename.find('-')
            first_underscore = filename.find('_')
            
            indices = [i for i in (first_dash, first_underscore) if i != -1]
            if indices:
                split_idx = min(indices)
                company_name = filename[:split_idx]
            else:
                # Fallback if no delimiter exists
                company_name = os.path.splitext(filename)[0]

            # Create the company-specific directory in the backup folder
            company_dir = os.path.join(backup_base_dir, company_name)
            os.makedirs(company_dir, exist_ok=True)
            
            # Inject date before _Resume if it exists, otherwise before extension
            if "_Resume" in filename:
                new_filename = filename.replace("_Resume", f"_{date_str}_Resume")
            else:
                name_part, ext_part = os.path.splitext(filename)
                new_filename = f"{name_part}_{date_str}{ext_part}"
                
            destination_path = os.path.join(company_dir, new_filename)
            shutil.copy2(file_path, destination_path)
            print(f"Copied: {filename} -> {os.path.join(company_name, new_filename)}")
            files_copied += 1

    if files_copied > 0:
        print(f"\nSuccess! Copied {files_copied} file(s) into company folders inside '{backup_base_dir}'")
    else:
        print(f"\nNo valid files were found in the '{source_dir}' folder to backup.")

if __name__ == "__main__":
    main()
