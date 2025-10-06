.DEFAULT_GOAL:=h

ROOT_DIR := $(dir $(lastword $(MAKEFILE_LIST)))
PKG_NAME := pulp_manager

.PHONY : h help
h help:
	@printf "%s\n" "Usage: make <target>"
	@printf "\n%s\n" "Targets:"
	@printf "    %-22s%s\n" \
	"h|help"	"Print this help" \
	"t|test"      	"Run all tests" \
	"l|lint"        "Run lint" \
	"c|cover"       "Run coverage for all tests" \
	"venv"          "Create virtualenv" \
	"clean"         "Clean workspace" \
	"demo"    "Run demo environment"

.PHONY : l lint
l lint: venv
	@echo "# pylint"; \
	./venv/bin/pylint --rcfile ./pylint.rc  pulp_manager/

check-devcontainer:
	@if [ -z "$$Is_local" ] && [ -z "$$DEVCONTAINER" ]; then \
		echo "ERROR: Tests must be run in devcontainer environment!"; \
		echo ""; \
		echo "To run tests:"; \
		echo "  1. Open VS Code"; \
		echo "  2. Use Command Palette (Cmd/Ctrl+Shift+P)"; \
		echo "  3. Select 'Dev Containers: Reopen in Container'"; \
		echo "  4. Wait for container to build"; \
		echo "  5. Run: make t"; \
		echo ""; \
		exit 1; \
	fi

.PHONY : t test
t test: venv check-devcontainer
	@./venv/bin/pytest -v

.PHONY : c cover
c cover: venv check-devcontainer
	@. venv/bin/activate; \
	coverage erase; \
	coverage run --source=. --omit=pulp_manager/tests/unit/mock_repository.py -m pytest -v && coverage report --fail-under=90; \
	coverage html

.PHONY : venv
venv: requirements.txt
	@python3 -m venv venv
	@. venv/bin/activate; \
	pip install --upgrade pip; \
	pip install -r requirements.txt

.PHONY : demo
demo: venv
	@echo "Setting up demo environment..."
	@. venv/bin/activate && \
		pip install -q ansible 'pulp-glue>=0.29.0' 'pulp-glue-deb>=0.3.0,<0.4' && \
		ansible-galaxy collection install pulp.squeezer 2>&1 | grep -v 'Installing' && \
		ansible-playbook -i localhost, demo/ansible/playbook.yml
