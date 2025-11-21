# Pulp Manager

![CI](https://github.com/G-Research/Pulp-manager/workflows/CI/badge.svg)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

## Project Description

The Pulp Manager application is used to coordinate common Pulp
workflows and provide additional reporting capabilities about a
cluster of Pulp servers. It is designed to work with Pulp3 servers in
a primary/secondary setup.

## Why Pulp Manager?

Pulp Manager provides centralized orchestration of a cluster of Pulp3
instances. It is particularly useful for organizations with
multi-tiered or multi-zone deployments who need coordinated syncs
between primary and secondary servers.

Pulp3 doesn't provide a method to schedule the synchronisation of
repos, and in some repository types (deb) may require multiple steps
to sync and update a repo's content. Pulp Manager provides the
coordination and reporting for this (along with other workflows),
rather than using a more generic management approach such as Ansible
or Jenkins.

## Core Team

This project originated at [G-Research](https://github.com/G-Research)
but is now owned by the Pulp project. For details on our team and
roles, please see the [MAINTAINERS.md](MAINTAINERS.md) file.

## Documentation Index

- [Quick Start](#quick-start) - Get up and running quickly
- [API Documentation](#api-documentation) - Interactive API docs
- [Application Configuration](#application-configuration) - Config
  file reference
- [Sync Configuration](#sync-configuration) - YAML config reference
- [Development Info](#development-info) - Development setup and
  workflow
- [Architecture](#architecture) - System design overview
- [Troubleshooting](#troubleshooting) - Common issues and solutions

## Repository Structure

The main code for the application lives in the app directory and split
into the following main folders:
- **models**: SQLAlchemy models which map back to database tables
- **repositories**: Classes that interact with the models. Each
  repository inherits from TableRepository, which contains generic
  operations for CRUD actions. On the filter method relationships
  directly attached to the entity can be eagerly loaded by specifying
  their name in the eager option. 1 model has 1 repository
- **services**: This contains the main business logic of the app,
  services will make use of multiple table repositories for
  interacting with the DB and also carry out the commits and rollbacks
- **utils**: Utilities common across the app, e.g. logging

## Development and Software Delivery Lifecycle

This project uses GitHub Actions for CI/CD with DevContainer
integration:

- **Automated Testing**: All tests run in the same DevContainer
  environment used for development
- **Linting**: Code quality checks with pylint
- **Coverage Reporting**: Test coverage analysis with pytest-cov (90%
  threshold required)
- **Multiple Test Strategies**: Both direct pytest and `make t`
  execution

The CI workflows are defined in `.github/workflows/`:
- `test.yml`: Quick test execution on pushes and PRs
- `ci.yml`: Comprehensive CI with linting, testing, and coverage
  reporting

## Why Synchronous APIs?

This application uses synchronous APIs rather than async for practical
reasons:
- Background jobs are handled by RQ workers (Redis Queue)
- RQ workers process one job at a time in a single process
- Database operations and external libraries (e.g., HashiCorp Vault)
  are synchronous
- The API doesn't experience high traffic volumes that would benefit
  from async handling

## Architecture

The application follows a layered architecture with five main
components:

1. **REST API** (port 8080 local, 443 production) - FastAPI
   application serving `/v1` endpoints
2. **Worker** - RQ-based background task processor for long-running
   operations
3. **Scheduler** - RQ scheduler for recurring tasks based on
   `pulp_config.yml`
4. **Exporter** (port 9300) - Prometheus metrics exporter for
   monitoring
5. **RQ Dashboard** - Web UI for monitoring background jobs

### Code Organization

- **`pulp_manager/app/`** - Main application code
  - **`models/`** - SQLAlchemy ORM models mapping to database tables
  - **`repositories/`** - Data access layer, each inheriting from
    `TableRepository` for CRUD operations
  - **`services/`** - Business logic layer coordinating repositories
    and external services
  - **`routers/v1/`** - FastAPI route definitions and request handlers
  - **`schemas/`** - Pydantic models for API request/response
    validation
  - **`tasks/`** - Background task definitions for RQ workers
  - **`auth/`** - LDAP authentication and JWT token handling

- **`pulp3_bindings/`** - Custom Pulp 3 API client library
- **`hashi_vault_client/`** - HashiCorp Vault integration for secrets
  management

### Key Services

- **PulpManager** - Core service orchestrating Pulp operations
- **Reconciler** - Ensures Pulp state matches configuration
- **RepoSyncher** - Manages repository synchronization
- **Snapshotter** - Creates repository snapshots
- **PulpConfigParser** - Processes `pulp_config.yml` configuration

## Quick Start

1. **For Development (running tests, exploring APIs, etc) **
   ```bash
   # Open in VS Code and select action "Dev Containers: Reopen in Container"
   # Or use the Dev Container CLI:
   devcontainer up --workspace-folder .
   ```
   
   From a terminal in the devcontainer, 'make t' will run the tests.
 

2. **For Demo cluster, use the make target to setup a complete Docker Compose environment**
   ```bash
   make demo
   ```

   When startup is finished, `docker ps` will show you the components, and all APIs will be listening.

### Demo Environment Details

Once the demo is running, you'll have access to:

**Available repositories on primary (http://localhost:8000):**
- `int-demo-packages`: http://localhost:8000/pulp/content/int-demo-packages/ (internal, no remote)
- `ext-demo-packages`: http://localhost:8000/pulp/content/ext-demo-packages/ (syncs from nginx.org)

**Available repositories on secondary (http://localhost:8001):**
- `int-demo-packages`: http://localhost:8001/pulp/content/int-demo-packages/ (syncs from primary)
- `ext-demo-packages`: http://localhost:8001/pulp/content/ext-demo-packages/ (syncs from primary)

**Services:**
- Pulp Manager API: http://localhost:8080
- RQ Dashboard: http://localhost:9181

### Demo Usage Examples

**Upload a package to int-demo-packages on primary:**
```bash
# Upload content
docker cp /path/to/package.deb demo-pulp-primary-1:/tmp/package.deb
docker exec demo-pulp-primary-1 pulp deb content upload --file /tmp/package.deb --repository int-demo-packages

# Create publication
docker exec demo-pulp-primary-1 pulp deb publication create --repository int-demo-packages --simple

# Update distribution (get publication href from above command)
docker exec demo-pulp-primary-1 pulp deb distribution update --name int-demo-packages --publication <publication_href>
```

**Sync ext-demo-packages on primary from nginx.org:**
```bash
curl -X POST 'http://localhost:8080/v1/pulp_servers/1/sync_repos' \
  -H 'Content-Type: application/json' \
  -d '{"max_runtime": "3600", "max_concurrent_syncs": 2, "regex_include": "^ext-demo"}'
```

**Sync int-demo-packages from primary to secondary:**
```bash
curl -X POST 'http://localhost:8080/v1/pulp_servers/2/sync_repos' \
  -H 'Content-Type: application/json' \
  -d '{"max_runtime": "3600", "max_concurrent_syncs": 2, "regex_include": "^int-demo", "source_pulp_server_name": "pulp-primary:80"}'
```

**Sync ext-demo-packages from primary to secondary:**
```bash
curl -X POST 'http://localhost:8080/v1/pulp_servers/2/sync_repos' \
  -H 'Content-Type: application/json' \
  -d '{"max_runtime": "3600", "max_concurrent_syncs": 2, "regex_include": "^ext-demo", "source_pulp_server_name": "pulp-primary:80"}'
```

For detailed development setup, see the [Development
Info](#development-info) section.

## API Documentation

Once the application is running, API documentation is available at:
- Swagger UI: http://localhost:8080/docs
- ReDoc: http://localhost:8080/redoc

## Application configuration

An ini file is used to define application settings. A sample ini along
with explanation of sections is given below. File needs to be deployed
to /etc/pulp_manager/config.ini

```
[ca]
root_ca_file_path=/etc/pulp_manager/root.pem

[auth]
method=ldap
use_ssl=true
ldap_servers=dc.example.com
base_dn=DC=example,DC=com
default_domain=example.com
jwt_algorithm=HS256
jwt_token_lifetime_mins=480
admin_group=pulpmaster-rw
require_jwt_auth=true

[pulp]
deb_signing_service=pulp_deb
banned_package_regex=bannedexample|another
internal_domains=example.com
git_repo_config=https://git.example.com/Pulp-Repo-Config
git_repo_config_dir=repo_config
password=password
internal_package_prefix=corp_
package_name_replacement_pattern=
package_name_replacement_rule=
remote_tls_validation=true
use_https_for_sync=true

[redis]
host=redis
port=6379
db=0
max_page_size=24

[remotes]
sock_connect_timeout=120.0
sock_read_timeout=600.0

[paging]
default_page_size=50
max_page_size=20000

[vault]
vault_addr=http://127.0.0.1:8200
repo_secret_namespace=secrets-common
```

### ca

Defines Certificate Authority settings
- `root_ca_file_path`: Path to root ca file, which is applied to
  remotes that are synched over SSL

### auth

Defines authentication allowed against the API
- `method`: Type of auth to use, currently only LDAP is allowed
- `use_ssl`: Specifies if LDAPS should be used
- `ldap_servers`: Comma separate list of LDAP servers to use for
  authentication
- `base_dn`: Base Distinguished Name to search for users in when
  carrying out authentication
- `default_domain`: Netbios name of the Active Directory domain
- `jwt_algorithm`: Algorithm to use to encrypt JWT tokens
- `jwt_token_lifetime_mins`: Number of minutes JWT is valid for
- `admin_group`: Directory group user must be a member of to carry out
  privileged actions against the API
- `require_jwt_auth`: Boolean whether to require JWT authentication for
  protected API endpoints. Set to false for local development environments
  where authentication is not needed. Defaults to true

### pulp

Settings to apply to all pulp servers
- `deb_signing_service`: Name of the signing service to use to sign
  Release file of deb repos
- `banned_package_regex`: Regex of packages that should be removed
  from externally synched repos
- `internal_domains`: Comma separated list of domains that are
  internal. Defines when the root CA cert should be applied, along
  with steps in synchronisation that can be synched when synching from
  a Pulp Server
- `git_repo_config`: Git repository URL that contains the
  configuration files for Pulp repos that should exist on primary
  servers
- `git_repo_config_dir`: Directory in `git_repo_config` which contains
  the pulp repo config
- `internal_package_prefix`: Prefix for indicating an internal package uploaded 
  directly to Pulp primary (no remote URL).
- `package_name_replacement_pattern`: Regex for matching packages to be
  renamed. Use named matching groups for use in the format rule.
- `package_name_replacement_rule`: The new name pattern assigned to packages
  which match the above. Reference named matching groups from above if needed.  
- `remote_tls_validation`: Boolean whether to require TLS validation
  of remote hosts
- `use_https_for_sync`: Boolean whether to use HTTPS for repository sync URLs.
  Set to false for local HTTP-only development environments. Defaults to true.

### redis

Settings to connect to redis
- `host`: hostname of the redis server
- `port`: port to connect to redis on
- `db`: db number to use
- `max_page_size`: Used via API to define maximum number of results
  that can be pulled back from redis at once

### remotes

Settings to all remotes created/update by Pulp Manager
- `sock_connect_timeout`: aiohttp.ClientTimeout.sock_connect (q.v.)
  for download-connections
- `sock_read_timeout`: aiohttp.ClientTimeout.sock_read (q.v.) for
  download-connections

### paging

Default settings for paging on the API
- `default_page_size`: Default size of pages retrieved from API
- `max_page_size`: Maximum number of results that can be returned in a
  single page

### vault

Settings for how Pulp Manager interacts with the vault agent
- `vault_addr`: Address to use to communicate with the vault agent
- `repo_secret_namespace`: namespace which contains remote
  secrets. This is where RedHat Certs and keys should be placed as
  defined in the repo config at
  https://git.example.com/Pulp-Repo-Config

## Sync Configuration

A YAML file needs to provided which the app reads on start up, to
insert the pulp servers and repo group information into the DB. File
needs to be deployed to /etc/pulp_manager/pulp_config.yml

A sample configuration file is shown below:

```
pulp_servers:
  pulp3.example.com:
    credentials: example
    repo_config_registration:
        schedule: "0,15,30,45 * * * *"
        max_runtime: "20m"
    repo_groups:
      external_repos:
        schedule: "0 0 * * *"
        max_concurrent_sync: 2
        max_runtime: "6h"
    snapshot_support:
      max_concurrent_snapshots: 2

  pulp3slav1.example.com:
    credentials: example
    repo_groups:
      external_repos:
        schedule: "0 6 * * *"
        max_concurrent_sync: 2
        max_runtime: "6h"
        pulp_master: pulp3mast1.example.com

credentials:
  example:
    username: svc-linux-pulp-dapi
    vault_service_account_mount: service-accounts

repo_groups:
  external_repos:
    regex_include: ^ext-
```

The different sections are as follows:

### pulp_servers

This is a dict which contains the name of the pulp server that is to
be managed, with the value being a series of dicts that define the
credentials and repo groups to sync.

- `credentials`: The name of the credentials block to use to retrieve
  credentials from HashiCorp Vault for authenticating against the Pulp
  Server API
- `repo_config_registration`: This is for use with pulp
  primaries. There is a Git repository which contains the base repos
  we expect to have on Pulp Servers
  (https://git.example.com/Pulp-Repo-Config). This repo defines remote
  repos that are used to sync external repos for the OS release along
  with internal repos. This parameter defines how often the config is
  checked out from Git and re-applied to the pulp server
  - `schedule`: cron syntax to define how often the job should run
  - `max_runtime`: how long the job should run for before it is
    cancelled
- `repo_groups`: Defines groups of repos that should be synched on a
  regular basis. The key is name of the repo group block to sync and
  the value is another dict which contains the options to use carrying
  out the syncs
  - `schedule`: cron syntax for how often the repo group should be
    synched
  - `max_concurrent_syncs`: How many repos should be synched at once
    when the job is run
  - `max_runtime`: How long the job should run for before it is
    cancelled
  - `pulp_master` (Optional): If the pulp server is synching the
    repos from a pulp primary, specify the name of the pulp
    server. This needs to exist in the pulp_servers config so that the
    list of repos on the server can be retrieved via the API
- `snapshot_support`: specifies if snapshots can be run against the
  pulp server, value is a dict
  - `max_concurrent_snapshots`: number of repos that can snapshotted
    simultaneously

### credentials

This is a dict which defines the name of credentials groups. The key
is a dict which names the credential group and the value, is another
dict which contains the configuration that pulp manager users to
retrieve the credentials from HashiCorp vault

- `username`: username of credentials group to retrieve
- `vault_service_account_mount`: vault service account path to
  retrieve the credentials form e.g. service-accounts
### repo_groups

This is a dict which defines a name for a set of repos, and then
regular expressions to match repo names on. The repo groups are then
applied to pulp server, which schedules and run times can be specified

- `regex_include` (Optional): regex to match repo names on that should
  be included for synchronisation
- `regex_exclude` (Optional): regex to match repo names that should be
  excluded from synchronisation. `regex_exclude` take precedence over
  `regex_include`, so if there is a repo that matches both regexes it
  would be excluded

## Example Repository Configuration

Repository configurations are stored in a Git repository and are
defined as JSON files. Here's an example structure:

### External Repository (e.g., CentOS):
```json
{
  "name": "centos-base",
  "content_repo_type": "rpm",
  "description": "CentOS 7 Base Repository",
  "owner": "Core Linux Engineering",
  "base_url": "centos7-x86_64",
  "url": "http://mirror.centos.org/centos/7/os/x86_64/",
  "proxy": "http://proxy.example.com:8080",
  "tls_validation": true
}
```

### Internal Repository:
```json
{
  "name": "myapp",
  "content_repo_type": "deb",
  "description": "Internal Application Packages",
  "owner": "Application Team",
  "base_url": "ubuntu-20.04-x86_64"
}
```

Repository names are automatically prefixed:
- External repos (with "url" field): get "ext-" prefix
- Internal repos: get the configured internal package prefix

## Local Development

### Prerequisites

- Docker and Docker Compose
- Python 3.10+ (for local development without containers)
- Git
- 8GB RAM minimum (for running all services)
- Visual Studio Code with Dev Containers extension (for DevContainer
  development)

### Development with DevContainers (Recommended)

This project includes a DevContainer configuration for consistent
development environments across different machines. DevContainers
provide a fully configured development environment with all necessary
dependencies, services, and tools pre-installed.

#### Getting Started with DevContainers

1. **Using VS Code (Recommended)**
   - Open the project in VS Code
   - When prompted, click "Reopen in Container"
   - Or use Command Palette (F1) → "Dev Containers: Reopen in
     Container"
   - VS Code will build and start the container with all services

2. **Using Command Line**
   ```bash
   # Install devcontainer CLI
   npm install -g @devcontainers/cli
   
   # Open in devcontainer
   devcontainer open
   ```

The DevContainer includes:
- Python 3.10 with all project dependencies
- MariaDB 11.1.2 for the database
- Redis for caching and task queuing
- LDAP development libraries
- Pre-configured pytest with VS Code integration
- All required Python packages from requirements.txt

#### Running Tests in DevContainer

Once inside the DevContainer:
```bash
# Run all tests
make t

# Run with coverage
make c

# Run specific test file
./venv/bin/pytest pulp_manager/tests/unit/test_job_manager.py -v

# Run lint
make l
```

### Alternative: Manual Development Setup

If you prefer not to use DevContainers, you can set up the development
environment manually:

1. **Starting the Development Environment**

To initialize and start the services required for local development,
execute the following command in your terminal:

```shell
make run-pulp-manager
```
This command starts the Pulp Manager API, worker, and RQ dashboard services along with their dependencies (MariaDB and Redis). It uses the demo/docker-compose.yml configuration. 

For local authentication, the Pulp manager utilizes the password
specified for pulp3 in local_config.ini and the username defined in
local_pulp_config.yml. Note that this configuration is contingent upon
the is_local environment variable being set to true. (This can be
found in pulp_helpers.py)

2. **Port Forwarding (Manual Setup Only)**

If using the manual setup, forward port 8080 from your environment to
your local machine. With DevContainers, ports are automatically
forwarded as configured in devcontainer.json.

For manual setups or DevPods:
```shell
devpod tunnel <name of your devpod> -p 8080:8080
```

3. **Accessing the Application**  

Once the development environment is up, you can access the application
through your web browser. Navigate to:

```
http://localhost:8080/docs
```

**Note**: With DevContainers, VS Code automatically handles port
forwarding for ports 8080, 9300, 3306, and 6379 as configured in the
devcontainer.json.
4. **Hot Reloading**  

The development environment is configured to support hot
reloading. This feature automatically refreshes your application as
soon as you make and save changes to the code. This means there's no
need to stop and restart the entire environment every time you modify
a file.


5. **Starting Pulp 3 Environment**

For development that requires Pulp 3, you can start the Pulp 3 environment. Run the following command in your terminal:
```
make run-pulp3
```
This command starts the Pulp 3 primary and secondary servers using the demo/docker-compose.yml configuration. It's particularly useful for testing integrations with Pulp 3 or when working on features that depend on Pulp 3 services.

#### When to Use make run-pulp-manager Again:

**Modifying Dependencies**: If your changes involve updating, adding,
or removing dependencies in your project, you will need to re-run the
make run-pulp-manager command. This ensures that the new dependencies
are correctly installed and integrated into your development
environment.

**Major Configuration Changes**: Similarly, for major changes to the
Docker configuration or other integral parts of the development setup
that are not automatically applied through hot reloading, re-running
make run-pulp-manager is necessary.

## Coding Style and Linters

Our coding standards and tools:

- **Coding Standards**: Follow existing code patterns in the
  codebase. Use repository pattern for data access, service layer for
  business logic, and dependency injection.
- **Linters**: We use pylint for Python code quality checks. Run with
  `make lint`.

## Branching Model

We use GitHub Flow with feature branches. Create a feature branch from
main, make your changes, and submit a pull request back to main.

## Contributing

Please read [CONTRIBUTING.md](CONTRIBUTING.md) for details on our
development workflow, coding standards, and how to submit pull
requests.

Please read [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for details on
our code of conduct.

### Development Workflow
1. Fork the repository
2. Create a feature branch (`git checkout -b amazing-feature`)
3. Make your changes
4. Run tests (`make t`)
5. Commit your changes following conventional commits
6. Push to your fork
7. Open a Pull Request

### Running Tests
```bash
# Run all tests
make t

# Run with coverage
make c

# Run linting
make l
```

## Codeowners

For details on code ownership and project maintainers, please see the
[MAINTAINERS.md](MAINTAINERS.md) file.

## Community

For development support and community discussions, please use:
- GitHub Issues: https://github.com/G-Research/Pulp-manager/issues
- GitHub Discussions:
  https://github.com/G-Research/Pulp-manager/discussions

## Community Guidelines

Please read [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for details on
acceptable conduct and community engagement guidelines.

## Governance

This project is governed by G-Research. For detailed governance
information, please see [GOVERNANCE.md](GOVERNANCE.md) if available.

## Continuous Integration

This project uses GitHub Actions for CI/CD with DevContainer
integration:

- **Automated Testing**: All tests run in the same DevContainer
  environment used for development
- **Linting**: Code quality checks with pylint
- **Coverage Reporting**: Test coverage analysis with pytest-cov
- **Multiple Test Strategies**: Both direct pytest and `make t`
  execution

The CI workflows are defined in `.github/workflows/`:
- `ci.yml`: Comprehensive CI with linting, testing, and coverage
  reporting

See `.github/workflows/README.md` for detailed workflow documentation.

## Troubleshooting

### Common Issues

1. **DevContainer fails to start**
   - Ensure Docker is running
   - Check port conflicts (8080, 3306, 6379)
   - Try: `docker system prune` to clean up resources

2. **Tests failing with database errors**
   - Database migrations may be needed
   - Run: `docker exec <container> alembic upgrade head`

3. **Authentication issues**
   - Check LDAP configuration in config.ini
   - Verify group membership matches `admin_group` setting

4. **Import errors in tests**
   - Ensure all dependencies are installed: `pip install -r
     requirements.txt`
   - Check that pulp3_bindings is installed: `pip install -e
     ./pulp3_bindings`

For more help, see
[Issues](https://github.com/G-Research/Pulp-manager/issues)

## Feedback

To provide feedback or report issues:
- **Bug Reports**: Please file bug reports at
  https://github.com/G-Research/Pulp-manager/issues
- **Feature Requests**: Submit feature requests at
  https://github.com/G-Research/Pulp-manager/issues
- **General Discussion**: Use GitHub Discussions at
  https://github.com/G-Research/Pulp-manager/discussions

## Glossary

- **Pulp**: Content management platform for software repositories
- **Pulp Primary**: Main Pulp server that syncs from external sources. Formerly known as Pulp master.
- **Pulp Secondary**: Pulp server that syncs from the primary. Formerly known as Pulp slave.
- **RQ**: Redis Queue - Python job queue library
- **LDAP**: Lightweight Directory Access Protocol for authentication
- **JWT**: JSON Web Token for API authentication
- **DevContainer**: Development environment configuration for VS Code

## License

This project is licensed under the Apache 2.0 License - see the
[LICENSE](LICENSE) file for details.

SPDX-License-Identifier: Apache-2.0
