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
        "setup-keys"    "Generates keys for use in local cluster" \
	"run-pulp3"	"Start Pulp 3 locally with Docker Compose" \
	"run-pulp-manager"    "Start Pulp Manager with Docker Compose" \
	"run-cluster"	      "Start Pulp3 + Pulp Manger local cluster with Docker Compose" \
	"setup-demo"    "Setup complete demo environment with repositories and packages"

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

venv: requirements.txt
	@python3 -m venv venv
	@. venv/bin/activate; \
	pip install --upgrade pip; \
	pip install -r requirements.txt

run-pulp-manager: setup-network setup-keys
	@echo "Starting Pulp Manager services..."
	docker compose -f demo/docker-compose.yml up mariadb redis-manager pulp-manager-api pulp-manager-worker pulp-manager-scheduler --build

.PHONY : run-pulp3
run-pulp3: setup-network
	@echo "Starting simplified Pulp 3 primary and secondary..."
	docker compose -f demo/docker-compose.yml up pulp-primary pulp-secondary --build

.PHONY : run-cluster
run-cluster: setup-network setup-keys
	@echo "Starting complete simplified cluster with Pulp Manager, Primary and Secondary Pulp instances..."
	docker compose -f demo/docker-compose.yml up --build

setup-network:
	@echo "Creating or verifying network..."
	docker network inspect pulp-net >/dev/null 2>&1 || \
	docker network create pulp-net
	@echo "Network setup completed."

setup-keys:
	@echo "Checking for Pulp encryption keys..."
	@mkdir -p demo/assets/certs demo/assets/keys demo/assets/nginx-conf
	@if [ ! -f demo/assets/certs/database_fields.symmetric.key ]; then \
		echo "Generating database encryption key..."; \
		openssl rand -base64 32 > demo/assets/certs/database_fields.symmetric.key; \
		echo "Database encryption key created."; \
	else \
		echo "Database encryption key already exists."; \
	fi
	@if [ ! -f demo/assets/keys/container_auth_private_key.pem ]; then \
		echo "Generating container auth keys..."; \
		openssl ecparam -genkey -name secp256r1 -noout -out demo/assets/keys/container_auth_private_key.pem; \
		openssl ec -in demo/assets/keys/container_auth_private_key.pem -pubout -out demo/assets/keys/container_auth_public_key.pem; \
		echo "Container auth keys created."; \
	else \
		echo "Container auth keys already exist."; \
	fi
	@mkdir -p demo/assets/keys/gpg
	@if [ ! -f demo/assets/keys/gpg/public.key ] || [ ! -s demo/assets/keys/gpg/public.key ]; then \
		echo "Generating GPG signing keys..."; \
		rm -rf demo/assets/keys/gpg/*; \
		chmod 700 demo/assets/keys/gpg; \
		echo "Key-Type: RSA" > /tmp/gpg-batch-config; \
		echo "Key-Length: 2048" >> /tmp/gpg-batch-config; \
		echo "Name-Real: Demo Signing Service" >> /tmp/gpg-batch-config; \
		echo "Name-Email: signing@pulp-demo.local" >> /tmp/gpg-batch-config; \
		echo "Expire-Date: 0" >> /tmp/gpg-batch-config; \
		echo "%no-protection" >> /tmp/gpg-batch-config; \
		echo "%commit" >> /tmp/gpg-batch-config; \
		GNUPGHOME=demo/assets/keys/gpg gpg --batch --no-default-keyring --keyring demo/assets/keys/gpg/pubring.kbx --gen-key /tmp/gpg-batch-config; \
		GNUPGHOME=demo/assets/keys/gpg gpg --no-default-keyring --keyring demo/assets/keys/gpg/pubring.kbx --armor --export > demo/assets/keys/gpg/public.key; \
		rm /tmp/gpg-batch-config; \
		echo "GPG signing keys created."; \
	else \
		echo "GPG signing keys already exist."; \
	fi

.PHONY : setup-demo
setup-demo:
	@echo "Setting up demo environment..."
	@docker run --rm \
		--network pulp-net \
		-v $(PWD)/demo/ansible:/ansible:ro \
		-v $(PWD)/demo/assets:/assets:ro \
		cytopia/ansible:latest \
		sh -c "pip3 install -q 'pulp-glue>=0.29.0' 'pulp-glue-deb>=0.3.0,<0.4' 2>&1 && \
		ansible-galaxy collection install pulp.squeezer 2>&1 | grep -v 'Installing' && \
		ansible-playbook -i localhost, /ansible/playbook.yml"
	@echo ""
	@echo "Demo Setup Complete"
	@echo "==================="
	@echo ""
	@echo "Available repositories:"
	@echo "  - ext-small-repo (external): http://localhost:8000/pulp/content/ext-small-repo/"
	@echo "  - int-demo-packages (internal): http://localhost:8000/pulp/content/int-demo-packages/"
	@echo ""
	@echo "Pulp Manager sync commands:"
	@echo "  # Sync internal repositories:"
	@echo "  curl -X POST 'http://localhost:8080/v1/pulp_servers/2/sync_repos' -H 'Content-Type: application/json' -d '{\"max_runtime\": \"3600\", \"max_concurrent_syncs\": 5, \"regex_include\": \"int-.*\", \"regex_exclude\": \"\"}'"
	@echo ""
	@echo "  # Sync external repositories:"
	@echo "  curl -X POST 'http://localhost:8080/v1/pulp_servers/2/sync_repos' -H 'Content-Type: application/json' -d '{\"max_runtime\": \"3600\", \"max_concurrent_syncs\": 5, \"regex_include\": \"ext-.*\", \"regex_exclude\": \"\"}'"
	@echo ""
	@echo "Monitor tasks: http://localhost:9181"
