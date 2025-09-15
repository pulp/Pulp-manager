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
        "setup-keys"    "Generates keys for use in local cluster" \
	"run-pulp3"	"Start Pulp 3 locally with Docker Compose" \
	"run-pulp-manager"    "Start Pulp Manager with Docker Compose" \
	"run-cluster"	      "Start Pulp3 + Pulp Manger local cluster with Docker Compose" \
	"upload-demo-package" "Upload demo deb package to local cluster pulp3-primary"

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

run-pulp-manager: setup-network setup-keys
	@echo "Starting Pulp Manager services..."
	docker compose -f docker/simple-cluster.yml up mariadb redis-manager pulp-manager-api pulp-manager-worker pulp-manager-scheduler --build

.PHONY : run-pulp3
run-pulp3: setup-network
	@echo "Starting simplified Pulp 3 primary and secondary..."
	docker compose -f docker/simple-cluster.yml up pulp-primary pulp-secondary --build

.PHONY : run-cluster
run-cluster: setup-network setup-keys
	@echo "Starting complete simplified cluster with Pulp Manager, Primary and Secondary Pulp instances..."
	docker compose -f docker/simple-cluster.yml up --build

setup-network:
	@echo "Creating or verifying network..."
	docker network inspect pulp-net >/dev/null 2>&1 || \
	docker network create pulp-net
	@echo "Network setup completed."

setup-keys:
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
	@mkdir -p assets/keys/gpg
	@if [ ! -f assets/keys/gpg/secring.gpg ]; then \
		echo "Generating GPG signing keys..."; \
		chmod 700 assets/keys/gpg; \
		echo "Key-Type: RSA" > /tmp/gpg-batch-config; \
		echo "Key-Length: 2048" >> /tmp/gpg-batch-config; \
		echo "Name-Real: Demo Signing Service" >> /tmp/gpg-batch-config; \
		echo "Name-Email: signing@pulp-demo.local" >> /tmp/gpg-batch-config; \
		echo "Expire-Date: 0" >> /tmp/gpg-batch-config; \
		echo "%no-protection" >> /tmp/gpg-batch-config; \
		echo "%commit" >> /tmp/gpg-batch-config; \
		GNUPGHOME=assets/keys/gpg gpg --batch --gen-key /tmp/gpg-batch-config; \
		GNUPGHOME=assets/keys/gpg gpg --armor --export > assets/keys/gpg/public.key; \
		rm /tmp/gpg-batch-config; \
		echo "GPG signing keys created."; \
	else \
		echo "GPG signing keys already exist."; \
	fi

.PHONY : upload-demo-package
upload-demo-package:
	@echo "Uploading demo package to pulp3-primary..."
	@./upload-package.sh
