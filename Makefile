.DEFAULT_GOAL:=h

ROOT_DIR := $(dir $(lastword $(MAKEFILE_LIST)))
PKG_NAME := pulp_manager

.PHONY : check-devcontainer
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
	"ansibe"        "Install ansible in venv" \
	"clean"         "Clean workspace" \
	"run-pulp-manager" "Run Pulp Manager services for development" \
	"run-pulp3"     "Run Pulp 3 primary and secondary servers" \
	"demo"          "Run complete demo environment" \
        "demo-repo-sync" "Upload package and run sync tasks in demo env"

.PHONY : l lint
l lint: venv
	@echo "# pylint"; \
	./venv/bin/pylint --rcfile ./pylint.rc  pulp_manager/

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
venv:
	@python3 -m venv venv
	@. venv/bin/activate; \
	pip install --upgrade pip -q; \
	pip install -r requirements.txt -q; 

.PHONY : ansible
ansible: venv
	@. venv/bin/activate && \
		pip install -q ansible 'pulp-glue>=0.29.0' 'pulp-glue-deb>=0.3.0,<0.4'

.PHONY : run-pulp-manager
run-pulp-manager:
	@echo "Starting Pulp Manager services for development..."
	@docker compose -f demo/docker-compose.yml up -d mariadb redis-manager
	@echo "Waiting for database to be ready..."
	@sleep 5
	@docker compose -f demo/docker-compose.yml up -d pulp-manager-api pulp-manager-worker pulp-manager-rq-dashboard
	@echo ""
	@echo "Pulp Manager services started!"
	@echo "API: http://localhost:8080/docs"
	@echo "RQ Dashboard: http://localhost:9181"

.PHONY : run-pulp3
run-pulp3:
	@echo "Starting Pulp 3 primary and secondary servers..."
	@docker compose -f demo/docker-compose.yml up -d pulp-primary pulp-secondary
	@echo ""
	@echo "Pulp 3 servers started!"
	@echo "Primary: http://localhost:8000"
	@echo "Secondary: http://localhost:8001"

.PHONY : demo
demo: venv ansible
	@echo "Setting up demo environment..."
	@. venv/bin/activate && \
		ansible-playbook -i localhost demo/ansible/playbook.yml

.PHONY : demo-repo-sync
demo-repo-sync: venv ansible
	@echo "Setting up demo environment..."
	@. venv/bin/activate && \
		ansible-playbook -i localhost demo/ansible/setup_repos.yml

