# Makefile for TailorTex

# Configuration variables
PYTHON = python
MAIN_SCRIPT = main.py
JD_FILE = job_description.txt
TEX_FILE = temp_resume.tex
NAME = Output
OUTPUT_DIR = output
CONSTRAINTS = true
PROJECTS = true
INPUT_DIR = output
SUFFIX_TEX = _Resume.tex

.PHONY: all run compile clean backup help serve-api serve-ui dev

# Default target
all: run

# Run the TailorTex pipeline
run:
	@echo "Running TailorTex pipeline with $(JD_FILE)..."
	$(PYTHON) $(MAIN_SCRIPT) --jd $(JD_FILE) --output "$(NAME)" \
	$(if $(filter true,$(CONSTRAINTS)),--constraints) \
	$(if $(filter true,$(PROJECTS)),--projects)
	@echo "Opening generated PDF..."
	@cmd /c start "" "$(OUTPUT_DIR)\$(NAME)_Resume.pdf"

# Manually compile a local LaTeX file
compile:
	@echo "Compiling $(TEX_FILE) into $(NAME)_Resume.pdf..."
	$(PYTHON) compile.py --tex "./$(INPUT_DIR)/$(NAME)$(SUFFIX_TEX)" --prefix "$(NAME)" --output "$(OUTPUT_DIR)"
	@echo "Opening generated PDF..."
	@cmd /c start "" "$(OUTPUT_DIR)\$(NAME)_Resume.pdf"

# Clean up generated files and inputs
clean:
	@echo "Cleaning up files in $(OUTPUT_DIR) and emptying $(JD_FILE)..."
	-@if exist "$(OUTPUT_DIR)" del /q "$(OUTPUT_DIR)\*.pdf" "$(OUTPUT_DIR)\*.tex" "$(OUTPUT_DIR)\*.aux" "$(OUTPUT_DIR)\*.log" "$(OUTPUT_DIR)\*.out"
	-@if exist "$(JD_FILE)" type nul > "$(JD_FILE)"
	@echo "Cleanup complete."

# Backup PDFs generated today to the location specified in .env
backup:
	@echo "Backing up today's PDFs..."
	$(PYTHON) backup.py

# Run FastAPI backend
serve-api:
	uvicorn api.server:app --reload --port 8000

# Run React frontend
serve-ui:
	cd frontend && npm run dev

# Run both dev servers in parallel
dev:
	make -j2 serve-api serve-ui

# Print help message
help:
	@echo "Usage:"
	@echo "  make run JD_FILE=<file> NAME=<name> [CONSTRAINTS=true] [PROJECTS=true]"
	@echo "  make compile TEX_FILE=<file> NAME=<name>"
	@echo "  make backup"
	@echo "  make clean"
