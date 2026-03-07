# Makefile for TailorTex

# Configuration variables
PYTHON = python
MAIN_SCRIPT = main.py
JD_FILE = job_description.txt
TEX_FILE = temp_resume.tex
NAME = Output
OUTPUT_DIR = output
CONSTRAINTS = true
PROJECTS = false

.PHONY: all run clean help

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
	$(PYTHON) compile.py --tex "$(TEX_FILE)" --prefix "$(NAME)" --output "$(OUTPUT_DIR)"
	@echo "Opening generated PDF..."
	@cmd /c start "" "$(OUTPUT_DIR)\$(NAME)_Resume.pdf"

# Clean up generated files and inputs
clean:
	@echo "Cleaning up $(OUTPUT_DIR) folder and $(JD_FILE)..."
	-@if exist "$(OUTPUT_DIR)" rmdir /s /q "$(OUTPUT_DIR)"
	-@if exist "$(JD_FILE)" del /f /q "$(JD_FILE)"
	@echo "Cleanup complete."

# Help menu
help:
	@echo "Available commands:"
	@echo "  make run    - Runs the TailorTex Python script with $(JD_FILE)"
	@echo "  make clean  - Deletes the $(OUTPUT_DIR) directory and $(JD_FILE)"
	@echo "  make help   - Displays this help message"
