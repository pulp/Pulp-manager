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

# Function to check if a resource exists by name
check_exists() {
    local url="$1"
    local name="$2"
    local response=$(curl -s -u $PULP_USER:$PULP_PASS "$url?name=$name" 2>/dev/null)
    
    # Check if curl failed or returned empty response
    if [ -z "$response" ] || ! echo "$response" | jq . > /dev/null 2>&1; then
        echo "Warning: Could not connect to server or invalid JSON response"
        return 1
    fi
    
    local count=$(echo "$response" | jq -r '.count // 0' 2>/dev/null)
    # Ensure count is a valid number
    if [[ "$count" =~ ^[0-9]+$ ]] && [ "$count" -gt 0 ]; then
        return 0
    else
        return 1
    fi
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

# Function to setup repository and remote on a server
setup_repo_and_remote() {
    local server="$1"
    local repo_name="$2"
    local repo_desc="$3"
    local remote_name="$4"
    local remote_url="$5"
    local distributions="$6"
    local components="$7"
    local architectures="$8"

    echo "   Setting up $repo_name on $(basename $server)..."

    # Create repository
    if check_exists "$server/pulp/api/v3/repositories/deb/apt/" "$repo_name"; then
        echo "     Repository '$repo_name' already exists"
        REPO_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS "$server/pulp/api/v3/repositories/deb/apt/?name=$repo_name")
        REPO_HREF=$(echo "$REPO_RESPONSE" | jq -r '.results[0].pulp_href')
    else
        echo "     Creating repository '$repo_name'..."
        REPO_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS -X POST "$server/pulp/api/v3/repositories/deb/apt/" \
            -H "Content-Type: application/json" \
            -d "{\"name\": \"$repo_name\", \"description\": \"$repo_desc\"}")
        REPO_HREF=$(echo "$REPO_RESPONSE" | jq -r '.pulp_href')
        echo "     Repository created: $REPO_HREF"
    fi

    # Only create remotes and associate them for secondary server (when remote_url is provided)
    if [ -n "$remote_url" ]; then
        # Create remote
        if check_exists "$server/pulp/api/v3/remotes/deb/apt/" "$remote_name"; then
            echo "     Remote '$remote_name' already exists"
            REMOTE_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS "$server/pulp/api/v3/remotes/deb/apt/?name=$remote_name")
            REMOTE_HREF=$(echo "$REMOTE_RESPONSE" | jq -r '.results[0].pulp_href')
        else
            echo "     Creating remote '$remote_name'..."
            remote_data="{\"name\": \"$remote_name\", \"url\": \"$remote_url\", \"distributions\": \"$distributions\""
            if [ -n "$components" ]; then
                remote_data="$remote_data, \"components\": \"$components\""
            fi
            if [ -n "$architectures" ]; then
                remote_data="$remote_data, \"architectures\": \"$architectures\""
            fi
            remote_data="$remote_data}"
            
            REMOTE_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS -X POST "$server/pulp/api/v3/remotes/deb/apt/" \
                -H "Content-Type: application/json" \
                -d "$remote_data")
            REMOTE_HREF=$(echo "$REMOTE_RESPONSE" | jq -r '.pulp_href')
            echo "     Remote created: $REMOTE_HREF"
        fi

        # Associate remote with repository
        echo "     Associating remote with repository..."
        REPO_UPDATE_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS -X PATCH "$server$REPO_HREF" \
            -H "Content-Type: application/json" \
            -d "{\"remote\": \"$REMOTE_HREF\"}")

        if echo "$REPO_UPDATE_RESPONSE" | jq -e '.task' > /dev/null; then
            TASK_HREF=$(echo "$REPO_UPDATE_RESPONSE" | jq -r '.task')
            wait_for_task "$TASK_HREF" "$server"
        fi
    fi

    echo "REPO_HREF_${repo_name//-/_}=$REPO_HREF"
    
    # Export variables for later use
    export "REPO_HREF_${repo_name//-/_}"="$REPO_HREF"
    if [ -n "$remote_url" ]; then
        export "REMOTE_HREF_${repo_name//-/_}"="$REMOTE_HREF"
    fi
}

# Function to update pulp-manager database with remote associations
update_pulp_manager_db() {
    local server_id="$1"
    local repo_name="$2" 
    local remote_href="$3"
    
    echo "  Updating pulp-manager database for $repo_name on server $server_id..."
    
    # Wait for pulp-manager to discover the repository first
    sleep 5
    
    # Get the pulp-manager repo ID by querying repos for the server
    local pm_repo_response=$(curl -s "http://localhost:8080/v1/pulp_servers/$server_id/repos")
    local pm_repo_id=$(echo "$pm_repo_response" | jq -r ".items[] | select(.name == \"$repo_name\") | .id")
    
    if [ -n "$pm_repo_id" ] && [ "$pm_repo_id" != "null" ]; then
        # Update the database directly
        docker exec docker-mariadb-1 mariadb -u pulp-manager -ppulp-manager pulp_manager \
            -e "UPDATE pulp_server_repos SET remote_href = '$remote_href' WHERE id = $pm_repo_id;"
        echo "     Updated pulp-manager database: repo ID $pm_repo_id -> $remote_href"
    else
        echo "      Could not find repo $repo_name in pulp-manager database for server $server_id"
        echo "    Available repos: $(echo "$pm_repo_response" | jq -r '.items[].name' | tr '\n' ' ')"
    fi
}

echo ""
echo "Checking server connectivity..."
check_server_running "$PULP_PRIMARY" "Pulp Primary"
check_server_running "$PULP_SECONDARY" "Pulp Secondary"

echo ""
echo "Step 1: Setting up Primary Server Repositories"
echo "=============================================="

# Setup external repo on primary with upstream remote
setup_repo_and_remote "$PULP_PRIMARY" "ext-small-repo" "External small Debian testing repository" \
    "debian-testing-remote" "http://deb.debian.org/debian/" "testing" "main" "amd64"
eval $(echo "REPO_HREF_ext_small_repo=$REPO_HREF")

# Setup internal repo on primary (no remote needed)
setup_repo_and_remote "$PULP_PRIMARY" "int-demo-packages" "Internal demo repository" "" "" "" "" ""
eval $(echo "REPO_HREF_int_demo_packages=$REPO_HREF")

echo ""
echo "Step 2: Setting up Secondary Server Repositories"
echo "==============================================="

# Setup repos on secondary that sync from primary
setup_repo_and_remote "$PULP_SECONDARY" "int-demo-packages" "Internal demo packages synced from primary" \
    "int-demo-remote" "http://docker-pulp-primary-1/pulp/content/int-demo-packages/" "stable" "" ""

setup_repo_and_remote "$PULP_SECONDARY" "ext-small-repo" "External small repo synced from primary" \
    "ext-small-remote" "http://docker-pulp-primary-1/pulp/content/ext-small-repo/" "testing" "main" "amd64"

echo ""
echo "Step 3: Uploading Demo Package to Internal Repository"
echo "====================================================="

# Check if package already exists in repository
INT_REPO_CONTENT=$(curl -s -u $PULP_USER:$PULP_PASS "$PULP_PRIMARY${INT_REPO_HREF}versions/1/content/")
HELLO_PKG_EXISTS=$(echo "$INT_REPO_CONTENT" | jq -r '.results[] | select(.summary and (.summary | contains("hello"))) | .pulp_href' | head -n1)

if [ -n "$HELLO_PKG_EXISTS" ] && [ "$HELLO_PKG_EXISTS" != "null" ]; then
    echo "   Demo package already exists in repository"
else
    echo "   Uploading demo package..."
    CONTENT_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS -X POST "$PULP_PRIMARY/pulp/api/v3/content/deb/packages/" \
        -H "Content-Type: multipart/form-data" \
        -F "file=@assets/packages/hello_2.10-2_amd64.deb")
    
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
echo "Step 4: Creating Publications"
echo "============================="

# Create publication for external repository
echo "   Creating publication for ext-small-repo..."
EXT_PUB_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS -X POST "$PULP_PRIMARY/pulp/api/v3/publications/deb/apt/" \
    -H "Content-Type: application/json" \
    -d "{\"repository\": \"$EXT_REPO_HREF\"}")

if echo "$EXT_PUB_RESPONSE" | jq -e '.task' > /dev/null; then
    EXT_PUB_TASK=$(echo "$EXT_PUB_RESPONSE" | jq -r '.task')
    wait_for_task "$EXT_PUB_TASK" "$PULP_PRIMARY"
    
    # Get publication href
    PUB_TASK_RESULT=$(curl -s -u $PULP_USER:$PULP_PASS "$PULP_PRIMARY$EXT_PUB_TASK")
    EXT_PUB_HREF=$(echo "$PUB_TASK_RESULT" | jq -r '.created_resources[0]')
    echo "   External publication created: $EXT_PUB_HREF"
else
    EXT_PUB_HREF=$(echo "$EXT_PUB_RESPONSE" | jq -r '.pulp_href')
    echo "   External publication exists: $EXT_PUB_HREF"
fi

# Create publication for internal repository
echo "   Creating publication for int-demo-packages..."
INT_PUB_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS -X POST "$PULP_PRIMARY/pulp/api/v3/publications/deb/apt/" \
    -H "Content-Type: application/json" \
    -d "{\"repository\": \"$INT_REPO_HREF\"}")

if echo "$INT_PUB_RESPONSE" | jq -e '.task' > /dev/null; then
    INT_PUB_TASK=$(echo "$INT_PUB_RESPONSE" | jq -r '.task')
    wait_for_task "$INT_PUB_TASK" "$PULP_PRIMARY"
    
    # Get publication href
    PUB_TASK_RESULT=$(curl -s -u $PULP_USER:$PULP_PASS "$PULP_PRIMARY$INT_PUB_TASK")
    INT_PUB_HREF=$(echo "$PUB_TASK_RESULT" | jq -r '.created_resources[0]')
    echo "   Internal publication created: $INT_PUB_HREF"
else
    INT_PUB_HREF=$(echo "$INT_PUB_RESPONSE" | jq -r '.pulp_href')
    echo "   Internal publication exists: $INT_PUB_HREF"
fi

echo ""
echo "Step 5: Creating Distributions"
echo "=============================="

# Create distribution for external repository
if check_exists "$PULP_PRIMARY/pulp/api/v3/distributions/deb/apt/" "ext-small-repo"; then
    echo "   Distribution 'ext-small-repo' already exists"
else
    echo "   Creating distribution for ext-small-repo..."
    EXT_DIST_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS -X POST "$PULP_PRIMARY/pulp/api/v3/distributions/deb/apt/" \
        -H "Content-Type: application/json" \
        -d "{
            \"name\": \"ext-small-repo\",
            \"base_path\": \"ext-small-repo\",
            \"publication\": \"$EXT_PUB_HREF\"
        }")
    
    if echo "$EXT_DIST_RESPONSE" | jq -e '.task' > /dev/null; then
        EXT_DIST_TASK=$(echo "$EXT_DIST_RESPONSE" | jq -r '.task')
        wait_for_task "$EXT_DIST_TASK" "$PULP_PRIMARY"
    fi
    echo "   External distribution created"
fi

# Create distribution for internal repository
if check_exists "$PULP_PRIMARY/pulp/api/v3/distributions/deb/apt/" "int-demo-packages"; then
    echo "   Distribution 'int-demo-packages' already exists"
else
    echo "   Creating distribution for int-demo-packages..."
    INT_DIST_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS -X POST "$PULP_PRIMARY/pulp/api/v3/distributions/deb/apt/" \
        -H "Content-Type: application/json" \
        -d "{
            \"name\": \"int-demo-packages\",
            \"base_path\": \"int-demo-packages\",
            \"publication\": \"$INT_PUB_HREF\"
        }")
    
    if echo "$INT_DIST_RESPONSE" | jq -e '.task' > /dev/null; then
        INT_DIST_TASK=$(echo "$INT_DIST_RESPONSE" | jq -r '.task')
        wait_for_task "$INT_DIST_TASK" "$PULP_PRIMARY"
    fi
    echo "   Internal distribution created"
fi

echo ""
echo "Step 6: Updating Pulp Manager Database"
echo "======================================"

# Update pulp-manager database with remote associations for secondary server (ID=2)
update_pulp_manager_db "2" "int-demo-packages" "$REMOTE_HREF_int_demo_packages"
update_pulp_manager_db "2" "ext-small-repo" "$REMOTE_HREF_ext_small_repo"

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