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
	"bdist"         "Create wheel file" \
	"clean"         "Clean workspace" \
	"run-pulp3"	"Start Pulp 3 locally with Docker Compose" \
	"run-pulp-manager" "Start Pulp Manager with Docker Compose" \
	"run-cluster"	"Start Pulp3 + Pulp Manger cluster with Docker Compose"

.PHONY : l lint
l lint: venv
	@echo "# pylint"; \
	./venv/bin/pylint --rcfile ./pylint.rc  pulp_manager/

.PHONY : t test
t test: venv
	@./venv/bin/pytest -v

.PHONY : c cover
c cover: venv
	@. venv/bin/activate; \
	coverage erase; \
	coverage run --source=. --omit=pulp_manager/tests/unit/mock_repository.py -m pytest -v && coverage report --fail-under=90; \
	coverage html

venv: requirements.txt
	@python3 -m venv venv
	@. venv/bin/activate; \
	pip install --upgrade pip; \
	pip install -r requirements.txt

run-pulp-manager: setup-network
	@echo "Starting local Docker Compose environment..."
	docker compose -f docker/local/docker-compose.yml up --build

.PHONY : run-pulp3
run-pulp3: setup-network setup-pulp-keys
	@echo "Starting Pulp 3 locally with Docker Compose..."
	docker compose -f docker/local/pulp-primary.yml up --build

.PHONY : run-cluster
run-cluster: setup-network setup-pulp-keys
	@echo "Starting complete local cluster with Pulp Manager, Primary and Secondary Pulp instances..."
	docker compose -f docker/local/docker-compose.yml \
	              -f docker/local/pulp-primary.yml \
	              -f docker/local/pulp-secondary.yml up --build

setup-network:
	@echo "Creating or verifying network..."
	docker network inspect pulp-net >/dev/null 2>&1 || \
	docker network create pulp-net
	@echo "Network setup completed."

setup-pulp-keys:
	@echo "Checking for Pulp encryption keys..."
	@mkdir -p assets/certs assets/keys assets/nginx-conf
	@if [ ! -f assets/certs/database_fields.symmetric.key ]; then \
		echo "Generating database encryption key..."; \
		openssl rand -base64 32 > assets/certs/database_fields.symmetric.key; \
		echo "Database encryption key created."; \
	else \
		echo "Database encryption key already exists."; \
	fi
	@if [ ! -f assets/keys/container_auth_private_key.pem ]; then \
		echo "Generating container auth keys..."; \
		openssl ecparam -genkey -name secp256r1 -noout -out assets/keys/container_auth_private_key.pem; \
		openssl ec -in assets/keys/container_auth_private_key.pem -pubout -out assets/keys/container_auth_public_key.pem; \
		echo "Container auth keys created."; \
	else \
		echo "Container auth keys already exist."; \
	fi
