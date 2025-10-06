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

.PHONY : setup-keys
setup-keys:
	@echo "Checking for Pulp encryption keys..."
	@mkdir -p demo/assets/certs demo/assets/keys/gpg demo/assets/nginx-conf
	@if [ ! -f demo/assets/certs/database_fields.symmetric.key ]; then \
		echo "Generating database encryption key..."; \
		openssl rand -base64 32 > demo/assets/certs/database_fields.symmetric.key; \
		echo "Database encryption key created."; \
	else \
		echo "Database encryption key already exists."; \
	fi
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
		GNUPGHOME=demo/assets/keys/gpg gpg-agent --daemon 2>/dev/null || true; \
		GNUPGHOME=demo/assets/keys/gpg gpg --batch --no-default-keyring --keyring demo/assets/keys/gpg/pubring.kbx --gen-key /tmp/gpg-batch-config; \
		GNUPGHOME=demo/assets/keys/gpg gpg --no-default-keyring --keyring demo/assets/keys/gpg/pubring.kbx --armor --export > demo/assets/keys/gpg/public.key; \
		GNUPGHOME=demo/assets/keys/gpg gpgconf --kill gpg-agent 2>/dev/null || true; \
		rm /tmp/gpg-batch-config; \
		echo "GPG signing keys created."; \
	else \
		echo "GPG signing keys already exist."; \
	fi

.PHONY : demo
demo: venv setup-keys
	@echo "Setting up demo environment..."
	@. venv/bin/activate && \
		pip install -q ansible 'pulp-glue>=0.29.0' 'pulp-glue-deb>=0.3.0,<0.4' && \
		ansible-galaxy collection install pulp.squeezer 2>&1 | grep -v 'Installing' && \
		ansible-playbook -i localhost, demo/ansible/playbook.yml
