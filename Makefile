# Makefile for TailorTex

# Configuration variables
PYTHON = python
MAIN_SCRIPT = local/main.py
JD_FILE = job_description.txt
TEX_FILE = temp_resume.tex
NAME = Output
OUTPUT_DIR = output
CONSTRAINTS = true
PROJECTS = true
INPUT_DIR = output
SUFFIX_TEX = _Resume.tex
VENV = venv

.PHONY: all run claude compile clean backup help serve-api serve-ui dev setup activate

# Default target
all: run

# Create virtual environment and install dependencies
setup:
	@echo "Creating virtual environment in ./venv ..."
	$(PYTHON) -m venv $(VENV)
	@echo "Installing requirements from requirements.txt ..."
	.\venv\Scripts\pip install -r requirements.txt
	@echo "Setup complete. Run 'make activate' for activation instructions."

# Activate the virtual environment
activate:
	@echo "To activate the environment, run:"
	@echo "source $(VENV)/Scripts/activate"

# Run the TailorTex pipeline
run:
	@echo "Running TailorTex pipeline with $(JD_FILE)..."
	$(PYTHON) $(MAIN_SCRIPT) --jd $(JD_FILE) --output "$(NAME)" \
	$(if $(filter true,$(CONSTRAINTS)),--constraints) \
	$(if $(filter true,$(PROJECTS)),--projects)
	@echo "Opening generated PDF..."
	@cmd /c start "" "$(OUTPUT_DIR)\$(NAME)_Resume.pdf"

# Run the TailorTex pipeline using Claude Code
claude:
	@echo "Running TailorTex pipeline with Claude Code for $(NAME)..."
	claude -p "/tailor-resume $(NAME)"

# Manually compile a local LaTeX file
compile:
	@echo "Compiling $(TEX_FILE) into $(NAME)_Resume.pdf..."
	$(PYTHON) local/compile.py --tex "./$(INPUT_DIR)/$(NAME)$(SUFFIX_TEX)" --prefix "$(NAME)" --output "$(OUTPUT_DIR)"
	@echo "Opening generated PDF..."
	@cmd /c start "" "$(OUTPUT_DIR)\$(NAME)_Resume.pdf"

# Clean up generated files and inputs
clean:
	@echo "Cleaning up files in $(OUTPUT_DIR) and emptying job description files..."
	-@if exist "$(OUTPUT_DIR)" del /q "$(OUTPUT_DIR)\*.pdf" "$(OUTPUT_DIR)\*.tex" "$(OUTPUT_DIR)\*.aux" "$(OUTPUT_DIR)\*.log" "$(OUTPUT_DIR)\*.out"
	-@if exist "$(OUTPUT_DIR)\extras" del /q "$(OUTPUT_DIR)\extras\*.*"
	-@if exist "$(JD_FILE)" type nul > "$(JD_FILE)"
	@echo "Cleanup complete."

# Backup PDFs generated today to the location specified in .env
backup:
	@echo "Backing up today's PDFs..."
	$(PYTHON) local/backup.py

# Run FastAPI backend
serve-api:
	cd backend && uvicorn api.server:app --reload --port 8001

# Run React frontend
serve-ui:
	cd frontend && npm run dev

# Run both dev servers in parallel
dev:
	make -j2 serve-api serve-ui

# Print help message
help:
	@echo "Usage:"
	@echo "  make setup                              - Create venv and install requirements.txt"
	@echo "  make activate                           - Activate the virtual environment"
	@echo "  make run JD_FILE=<file> NAME=<name> [CONSTRAINTS=true] [PROJECTS=true]"
	@echo "  make claude NAME=<name>"
	@echo "  make compile TEX_FILE=<file> NAME=<name>"
	@echo "  make backup"
	@echo "  make clean"
