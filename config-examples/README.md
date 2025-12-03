# Configuration Examples

This directory contains example configuration files that are copied into the Docker image at `/etc/pulp_manager/`.

## Files

- `config.ini` - Main application configuration
- `pulp_config.yml` - Pulp server and sync schedule configuration

## Usage

### Docker Image Default Configs

These configs are automatically copied to `/etc/pulp_manager/` during image build and serve as working defaults for testing and CI.

### Production Deployment

For production use, you have two options:

#### Option 1: Mount custom configs at the default location

```bash
docker run -v /path/to/your/config.ini:/etc/pulp_manager/config.ini \
           -v /path/to/your/pulp_config.yml:/etc/pulp_manager/pulp_config.yml \
           pulp/pulp-manager
```

#### Option 2: Mount configs anywhere and use environment variables

```bash
docker run -v /path/to/configs:/configs \
           -e PULP_MANAGER_CONFIG_PATH=/configs/my-config.ini \
           -e PULP_SYNC_CONFIG_PATH=/configs/my-pulp-config.yml \
           pulp/pulp-manager
```

### Environment Variable Overrides

The default `config.ini` supports environment variable substitution for common settings:

- `DB_HOSTNAME` - Database host (default: localhost)
- `DB_PORT` - Database port (default: 3306)
- `DB_NAME` - Database name (default: pulp_manager)
- `DB_USER` - Database user (default: pulp-manager)
- `DB_PASSWORD` - Database password (default: pulp-manager)
- `REDIS_HOST` - Redis host (default: localhost)
- `REDIS_PORT` - Redis port (default: 6379)
- `REDIS_DB` - Redis database number (default: 0)
- `PULP_ADMIN_PASSWORD` - Pulp admin password (default: password)

Example using environment variables:

```bash
docker run -e DB_HOSTNAME=mysql.example.com \
           -e DB_PASSWORD=secret \
           -e REDIS_HOST=redis.example.com \
           pulp/pulp-manager
```

## Configuration Locations

The application looks for configs in this order:

1. Environment variable `PULP_MANAGER_CONFIG_PATH` (if set)
2. Default location `/etc/pulp_manager/config.ini`

And for Pulp sync config:

1. Environment variable `PULP_SYNC_CONFIG_PATH` (if set)
2. Default location `/etc/pulp_manager/pulp_config.yml`
