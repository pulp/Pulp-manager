#!/bin/bash

set -e

PULP_PRIMARY="http://localhost:8000"
PULP_SECONDARY="http://localhost:8001"
PULP_USER="admin"
PULP_PASS="password"

echo "Setting up Pulp Demo Environment"
echo "================================="

# Function to check if a server is running and accessible
check_server_running() {
    local server="$1"
    local server_name="$2"

    echo "Checking if $server_name is running..."

    if ! curl -s --connect-timeout 5 "$server/pulp/api/v3/status/" > /dev/null 2>&1; then
        echo "ERROR: $server_name at $server is not accessible!"
        echo ""
        echo "To fix this issue:"
        echo "  1. Start the Pulp cluster: make run-cluster"
        echo "  2. Wait for services to be healthy (may take 1-2 minutes)"
        echo "  3. Then run: make setup-demo"
        echo ""
        return 1
    fi

    echo "$server_name is running and accessible"
    return 0
}

# Function to wait for task completion
wait_for_task() {
    local task_href="$1"
    local server="$2"
    echo "  Waiting for task to complete..."
    
    while true; do
        local task_result=$(curl -s -u $PULP_USER:$PULP_PASS "$server$task_href")
        local state=$(echo "$task_result" | jq -r '.state')
        
        case "$state" in
            "completed")
                echo "   Task completed successfully"
                return 0
                ;;
            "failed")
                echo "   Task failed!"
                echo "  Error: $(echo "$task_result" | jq -r '.error.description // .error')"
                return 1
                ;;
            "running"|"waiting")
                echo "   Task $state, waiting..."
                sleep 2
                ;;
            *)
                echo "   Task in state: $state, waiting..."
                sleep 2
                ;;
        esac
    done
}

echo ""
echo "Checking server connectivity..."
check_server_running "$PULP_PRIMARY" "Pulp Primary"
check_server_running "$PULP_SECONDARY" "Pulp Secondary"

# Function to register signing service in Pulp
assign_signing_service_to_repo() {
    local server="$1"
    local repo_href="$2"
    local repo_name="$3"
    
    # Get the signing service href for this server
    local signing_service_href=$(curl -s -u $PULP_USER:$PULP_PASS "$server/pulp/api/v3/signing-services/?name=deb_signing_service" | jq -r '.results[0].pulp_href')
    
    if [ -n "$signing_service_href" ] && [ "$signing_service_href" != "null" ]; then
        echo "     Assigning signing service to $repo_name..."
        curl -s -u $PULP_USER:$PULP_PASS -X PATCH "$server$repo_href" \
            -H "Content-Type: application/json" \
            -d "{\"signing_service\": \"$signing_service_href\"}" > /dev/null
        echo "     Signing service assigned: $signing_service_href"
    fi
}

register_signing_service() {
    local container_name="$1"
    local server_name="$2"

    echo "  Checking if signing service exists on $server_name..."

    # Check if signing service already exists in the database
    existing=$(docker exec $container_name bash -c "pulpcore-manager shell -c \"from pulpcore.app.models import SigningService; print(SigningService.objects.filter(name='deb_signing_service').exists())\"" 2>/dev/null)

    if [ "$existing" = "True" ]; then
        echo "  Signing service 'deb_signing_service' already exists on $server_name"
    else
        echo "  Creating signing service 'deb_signing_service' on $server_name..."

        # Get the GPG key ID from the mounted keyring
        key_id=$(docker exec $container_name bash -c "GNUPGHOME=/opt/gpg gpg --list-secret-keys --with-colons 2>/dev/null | grep '^sec:' | cut -d: -f5 | head -1")

        if [ -n "$key_id" ]; then
            # Add the signing service using the management command
            docker exec $container_name bash -c "pulpcore-manager add-signing-service \
                'deb_signing_service' \
                '/opt/scripts/deb_sign.sh' \
                '$key_id' \
                --gnupghome /opt/gpg" 2>/dev/null && \
            echo "  Signing service created successfully on $server_name" || \
            echo "  Failed to create signing service on $server_name"
        else
            echo "  No GPG key found in /opt/gpg on $server_name"
        fi
    fi
}

echo ""
echo "Setting up signing services..."
echo "=============================="
register_signing_service "demo-pulp-primary-1" "Pulp Primary"
register_signing_service "demo-pulp-secondary-1" "Pulp Secondary"

echo ""
echo "Step 1: Getting Repository References"
echo "====================================="

# Get repository hrefs (assumes repos already exist in Pulp)
echo "  Getting int-demo-packages repository..."
REPO_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS "$PULP_PRIMARY/pulp/api/v3/repositories/deb/apt/?name=int-demo-packages")
INT_REPO_HREF=$(echo "$REPO_RESPONSE" | jq -r '.results[0].pulp_href')

if [ -z "$INT_REPO_HREF" ] || [ "$INT_REPO_HREF" = "null" ]; then
    echo "  ERROR: Repository 'int-demo-packages' not found on primary server"
    echo "  Please create repositories manually or via Pulp Manager before running this script"
    exit 1
fi
echo "  Found: $INT_REPO_HREF"

echo "  Getting ext-small-repo repository..."
REPO_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS "$PULP_PRIMARY/pulp/api/v3/repositories/deb/apt/?name=ext-small-repo")
EXT_REPO_HREF=$(echo "$REPO_RESPONSE" | jq -r '.results[0].pulp_href')

if [ -z "$EXT_REPO_HREF" ] || [ "$EXT_REPO_HREF" = "null" ]; then
    echo "  ERROR: Repository 'ext-small-repo' not found on primary server"
    exit 1
fi
echo "  Found: $EXT_REPO_HREF"

echo ""
echo "Step 2: Uploading Demo Package to Internal Repository"
echo "====================================================="

# Check if package already exists in repository
# First get the latest version href
LATEST_VERSION=$(curl -s -u $PULP_USER:$PULP_PASS "$PULP_PRIMARY${INT_REPO_HREF}versions/?limit=1&ordering=-number" | jq -r '.results[0].pulp_href' 2>/dev/null)
if [ -n "$LATEST_VERSION" ] && [ "$LATEST_VERSION" != "null" ]; then
    INT_REPO_CONTENT=$(curl -s -u $PULP_USER:$PULP_PASS "$PULP_PRIMARY${LATEST_VERSION}content/?limit=100")
    HELLO_PKG_EXISTS=$(echo "$INT_REPO_CONTENT" | jq -r '.results[] | select(.relative_path and (.relative_path | contains("hello"))) | .pulp_href' 2>/dev/null | head -n1)
else
    HELLO_PKG_EXISTS=""
fi

if [ -n "$HELLO_PKG_EXISTS" ] && [ "$HELLO_PKG_EXISTS" != "null" ]; then
    echo "   Demo package already exists in repository"
else
    echo "   Uploading demo package..."
    CONTENT_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS -X POST "$PULP_PRIMARY/pulp/api/v3/content/deb/packages/" \
        -H "Content-Type: multipart/form-data" \
        -F "file=@demo/assets/packages/hello_2.10-2_amd64.deb")
    
    UPLOAD_TASK_HREF=$(echo "$CONTENT_RESPONSE" | jq -r '.task')
    wait_for_task "$UPLOAD_TASK_HREF" "$PULP_PRIMARY"
    
    # Get content href from completed task
    TASK_RESULT=$(curl -s -u $PULP_USER:$PULP_PASS "$PULP_PRIMARY$UPLOAD_TASK_HREF")
    CONTENT_HREF=$(echo "$TASK_RESULT" | jq -r '.created_resources[0]')
    echo "   Content created: $CONTENT_HREF"
    
    # Add content to repository
    echo "   Adding content to repository..."
    MODIFY_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS -X POST "$PULP_PRIMARY${INT_REPO_HREF}modify/" \
        -H "Content-Type: application/json" \
        -d "{\"add_content_units\": [\"$CONTENT_HREF\"]}")
    
    MODIFY_TASK_HREF=$(echo "$MODIFY_RESPONSE" | jq -r '.task')
    wait_for_task "$MODIFY_TASK_HREF" "$PULP_PRIMARY"
fi

echo ""
echo "Step 3: Creating Publications and Distributions"
echo "==============================================="

# Create publication for internal repository
echo "  Creating publication for int-demo-packages..."
INT_PUB_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS -X POST "$PULP_PRIMARY/pulp/api/v3/publications/deb/apt/" \
    -H "Content-Type: application/json" \
    -d "{\"repository\": \"$INT_REPO_HREF\", \"simple\": true}")

if echo "$INT_PUB_RESPONSE" | jq -e '.task' > /dev/null; then
    INT_PUB_TASK=$(echo "$INT_PUB_RESPONSE" | jq -r '.task')
    wait_for_task "$INT_PUB_TASK" "$PULP_PRIMARY"

    # Get publication href
    PUB_TASK_RESULT=$(curl -s -u $PULP_USER:$PULP_PASS "$PULP_PRIMARY$INT_PUB_TASK")
    INT_PUB_HREF=$(echo "$PUB_TASK_RESULT" | jq -r '.created_resources[0]')
    echo "  Publication created: $INT_PUB_HREF"
fi

# Create or update distribution for internal repository
DIST_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS "$PULP_PRIMARY/pulp/api/v3/distributions/deb/apt/?name=int-demo-packages")
DIST_EXISTS=$(echo "$DIST_RESPONSE" | jq -r '.count')

if [ "$DIST_EXISTS" -gt 0 ]; then
    echo "  Updating distribution for int-demo-packages..."
    DIST_HREF=$(echo "$DIST_RESPONSE" | jq -r '.results[0].pulp_href')
    DIST_UPDATE=$(curl -s -u $PULP_USER:$PULP_PASS -X PATCH "$PULP_PRIMARY$DIST_HREF" \
        -H "Content-Type: application/json" \
        -d "{\"publication\": \"$INT_PUB_HREF\"}")

    if echo "$DIST_UPDATE" | jq -e '.task' > /dev/null; then
        DIST_TASK=$(echo "$DIST_UPDATE" | jq -r '.task')
        wait_for_task "$DIST_TASK" "$PULP_PRIMARY"
    fi
else
    echo "  Creating distribution for int-demo-packages..."
    DIST_CREATE=$(curl -s -u $PULP_USER:$PULP_PASS -X POST "$PULP_PRIMARY/pulp/api/v3/distributions/deb/apt/" \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"int-demo-packages\", \"base_path\": \"int-demo-packages\", \"publication\": \"$INT_PUB_HREF\"}")

    if echo "$DIST_CREATE" | jq -e '.task' > /dev/null; then
        DIST_TASK=$(echo "$DIST_CREATE" | jq -r '.task')
        wait_for_task "$DIST_TASK" "$PULP_PRIMARY"
    fi
fi
echo "  Distribution ready for int-demo-packages"

echo ""
echo " Demo Setup Complete!"
echo "======================"
echo ""
echo "Available repositories:"
echo "   ext-small-repo (external): $PULP_PRIMARY/pulp/content/ext-small-repo/"
echo "   int-demo-packages (internal): $PULP_PRIMARY/pulp/content/int-demo-packages/"
echo ""
echo "Pulp Manager sync commands:"
echo "  # Sync internal repositories:"
echo "  curl -X POST 'http://localhost:8080/v1/pulp_servers/2/sync_repos' -H 'Content-Type: application/json' -d '{\"max_runtime\": \"3600\", \"max_concurrent_syncs\": 5, \"regex_include\": \"int-.*\", \"regex_exclude\": \"\"}'"
echo ""
echo "  # Sync external repositories:"  
echo "  curl -X POST 'http://localhost:8080/v1/pulp_servers/2/sync_repos' -H 'Content-Type: application/json' -d '{\"max_runtime\": \"3600\", \"max_concurrent_syncs\": 5, \"regex_include\": \"ext-.*\", \"regex_exclude\": \"\"}'"
echo ""
echo "Monitor tasks: http://localhost:9181"