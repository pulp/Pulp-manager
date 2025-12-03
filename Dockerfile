# Base stage for common environment setup
FROM ubuntu:24.04 as base

LABEL description="Pulp Manager container for workers and API "

ENV PATH="/opt/venv/bin:$PATH"
ENV CURL_CA_BUNDLE="/etc/ssl/certs/ca-certificates.crt"
ENV REQUESTS_CA_BUNDLE="/etc/ssl/certs/ca-certificates.crt"

RUN mkdir -p /pulp_manager \
	&& groupadd pulp_manager \
	&& useradd -u 10001 pulp_manager -g pulp_manager -d /pulp_manager/ \
	&& apt-get update \
	&& apt-get install -y python3-venv netcat-openbsd \
	&& python3 -m venv /opt/venv

WORKDIR /pulp_manager

# Builder stage for installing dependencies
FROM base as builder

# Install build dependencies
RUN apt-get install -y python3-dev default-libmysqlclient-dev build-essential git libsasl2-dev libldap2-dev libssl-dev

# Copy only the requirements file to avoid cache invalidation
COPY requirements.txt .

# Install Python dependencies
RUN /opt/venv/bin/pip install --upgrade pip \
	&& /opt/venv/bin/pip install -r requirements.txt

# Final stage for the application
FROM base as final

# Install runtime dependencies including Git and make
RUN apt-get update && apt-get install -y netcat-openbsd git make python3-dev libsasl2-dev libldap2-dev libssl-dev default-libmysqlclient-dev build-essential && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv
# Copy requirements file
COPY --from=builder /pulp_manager/requirements.txt ./

# Copy application code
COPY pulp_manager ./pulp_manager/
COPY pulp3_bindings ./pulp3_bindings/
COPY hashi_vault_client ./hashi_vault_client/

# Copy database migration files
COPY alembic ./alembic/
COPY alembic.ini ./

# Copy entrypoint script
COPY pulp-manager.sh ./

# Install default configs to /etc/pulp_manager/
RUN mkdir -p /etc/pulp_manager
COPY config-examples/config.ini /etc/pulp_manager/config.ini
COPY config-examples/pulp_config.yml /etc/pulp_manager/pulp_config.yml

# Ensure correct permissions
RUN chown -R pulp_manager:pulp_manager /pulp_manager \
    && chown -R pulp_manager:pulp_manager /etc/pulp_manager \
    && ln -s /pulp_manager/pulp-manager.sh /usr/local/bin/pulp-manager

USER 10001
