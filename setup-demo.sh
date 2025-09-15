#!/bin/bash

set -e

PULP_PRIMARY="http://localhost:8000"
PULP_SECONDARY="http://localhost:8001"
PULP_USER="admin"
PULP_PASS="password"

echo "🚀 Setting up Pulp Demo Environment"
echo "=================================="

# Function to check if a resource exists by name
check_exists() {
    local url="$1"
    local name="$2"
    local response=$(curl -s -u $PULP_USER:$PULP_PASS "$url?name=$name")
    local count=$(echo "$response" | jq -r '.count // 0')
    [ "$count" -gt 0 ]
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
                echo "  ✅ Task completed successfully"
                return 0
                ;;
            "failed")
                echo "  ❌ Task failed!"
                echo "  Error: $(echo "$task_result" | jq -r '.error.description // .error')"
                return 1
                ;;
            "running"|"waiting")
                echo "  ⏳ Task $state, waiting..."
                sleep 2
                ;;
            *)
                echo "  ⏳ Task in state: $state, waiting..."
                sleep 2
                ;;
        esac
    done
}

echo ""
echo "Step 1: Creating External Repository (ext-small-repo)"
echo "===================================================="

# Create external repository
if check_exists "$PULP_PRIMARY/pulp/api/v3/repositories/deb/apt/" "ext-small-repo"; then
    echo "  ✅ Repository 'ext-small-repo' already exists"
    EXT_REPO_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS "$PULP_PRIMARY/pulp/api/v3/repositories/deb/apt/?name=ext-small-repo")
    EXT_REPO_HREF=$(echo "$EXT_REPO_RESPONSE" | jq -r '.results[0].pulp_href')
else
    echo "  📦 Creating repository 'ext-small-repo'..."
    EXT_REPO_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS -X POST "$PULP_PRIMARY/pulp/api/v3/repositories/deb/apt/" \
        -H "Content-Type: application/json" \
        -d '{"name": "ext-small-repo", "description": "External small Debian testing repository"}')
    EXT_REPO_HREF=$(echo "$EXT_REPO_RESPONSE" | jq -r '.pulp_href')
    echo "  ✅ Repository created: $EXT_REPO_HREF"
fi

# Create remote for Debian testing with limited components
if check_exists "$PULP_PRIMARY/pulp/api/v3/remotes/deb/apt/" "debian-testing-remote"; then
    echo "  ✅ Remote 'debian-testing-remote' already exists"
    REMOTE_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS "$PULP_PRIMARY/pulp/api/v3/remotes/deb/apt/?name=debian-testing-remote")
    REMOTE_HREF=$(echo "$REMOTE_RESPONSE" | jq -r '.results[0].pulp_href')
else
    echo "  🌐 Creating remote for Debian testing (limited components)..."
    REMOTE_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS -X POST "$PULP_PRIMARY/pulp/api/v3/remotes/deb/apt/" \
        -H "Content-Type: application/json" \
        -d '{
            "name": "debian-testing-remote",
            "url": "http://deb.debian.org/debian/",
            "distributions": "testing",
            "components": "main",
            "architectures": "amd64",
            "sync_sources": false,
            "sync_udebs": false,
            "sync_installer": false
        }')
    REMOTE_HREF=$(echo "$REMOTE_RESPONSE" | jq -r '.pulp_href')
    echo "  ✅ Remote created: $REMOTE_HREF"
fi

# Associate remote with repository
echo "  🔗 Associating remote with repository..."
REPO_UPDATE_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS -X PATCH "$PULP_PRIMARY$EXT_REPO_HREF" \
    -H "Content-Type: application/json" \
    -d "{\"remote\": \"$REMOTE_HREF\"}")

if echo "$REPO_UPDATE_RESPONSE" | jq -e '.task' > /dev/null; then
    TASK_HREF=$(echo "$REPO_UPDATE_RESPONSE" | jq -r '.task')
    wait_for_task "$TASK_HREF" "$PULP_PRIMARY"
fi

echo ""
echo "Step 2: Creating Internal Repository (int-demo-packages)"
echo "======================================================="

# Create internal repository
if check_exists "$PULP_PRIMARY/pulp/api/v3/repositories/deb/apt/" "int-demo-packages"; then
    echo "  ✅ Repository 'int-demo-packages' already exists"
    INT_REPO_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS "$PULP_PRIMARY/pulp/api/v3/repositories/deb/apt/?name=int-demo-packages")
    INT_REPO_HREF=$(echo "$INT_REPO_RESPONSE" | jq -r '.results[0].pulp_href')
else
    echo "  📦 Creating repository 'int-demo-packages'..."
    INT_REPO_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS -X POST "$PULP_PRIMARY/pulp/api/v3/repositories/deb/apt/" \
        -H "Content-Type: application/json" \
        -d '{"name": "int-demo-packages", "description": "Internal demo repository"}')
    INT_REPO_HREF=$(echo "$INT_REPO_RESPONSE" | jq -r '.pulp_href')
    echo "  ✅ Repository created: $INT_REPO_HREF"
fi

echo ""
echo "Step 3: Uploading Demo Package to Internal Repository"
echo "====================================================="

# Check if package already exists in repository
INT_REPO_CONTENT=$(curl -s -u $PULP_USER:$PULP_PASS "$PULP_PRIMARY${INT_REPO_HREF}versions/1/content/")
HELLO_PKG_EXISTS=$(echo "$INT_REPO_CONTENT" | jq -r '.results[] | select(.summary and (.summary | contains("hello"))) | .pulp_href' | head -n1)

if [ -n "$HELLO_PKG_EXISTS" ] && [ "$HELLO_PKG_EXISTS" != "null" ]; then
    echo "  ✅ Demo package already exists in repository"
else
    echo "  📤 Uploading demo package..."
    CONTENT_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS -X POST "$PULP_PRIMARY/pulp/api/v3/content/deb/packages/" \
        -H "Content-Type: multipart/form-data" \
        -F "file=@assets/packages/hello_2.10-2_amd64.deb")
    
    UPLOAD_TASK_HREF=$(echo "$CONTENT_RESPONSE" | jq -r '.task')
    wait_for_task "$UPLOAD_TASK_HREF" "$PULP_PRIMARY"
    
    # Get content href from completed task
    TASK_RESULT=$(curl -s -u $PULP_USER:$PULP_PASS "$PULP_PRIMARY$UPLOAD_TASK_HREF")
    CONTENT_HREF=$(echo "$TASK_RESULT" | jq -r '.created_resources[0]')
    echo "  📦 Content created: $CONTENT_HREF"
    
    # Add content to repository
    echo "  ➕ Adding content to repository..."
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
echo "  📰 Creating publication for ext-small-repo..."
EXT_PUB_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS -X POST "$PULP_PRIMARY/pulp/api/v3/publications/deb/apt/" \
    -H "Content-Type: application/json" \
    -d "{\"repository\": \"$EXT_REPO_HREF\"}")

if echo "$EXT_PUB_RESPONSE" | jq -e '.task' > /dev/null; then
    EXT_PUB_TASK=$(echo "$EXT_PUB_RESPONSE" | jq -r '.task')
    wait_for_task "$EXT_PUB_TASK" "$PULP_PRIMARY"
    
    # Get publication href
    PUB_TASK_RESULT=$(curl -s -u $PULP_USER:$PULP_PASS "$PULP_PRIMARY$EXT_PUB_TASK")
    EXT_PUB_HREF=$(echo "$PUB_TASK_RESULT" | jq -r '.created_resources[0]')
    echo "  ✅ External publication created: $EXT_PUB_HREF"
else
    EXT_PUB_HREF=$(echo "$EXT_PUB_RESPONSE" | jq -r '.pulp_href')
    echo "  ✅ External publication exists: $EXT_PUB_HREF"
fi

# Create publication for internal repository
echo "  📰 Creating publication for int-demo-packages..."
INT_PUB_RESPONSE=$(curl -s -u $PULP_USER:$PULP_PASS -X POST "$PULP_PRIMARY/pulp/api/v3/publications/deb/apt/" \
    -H "Content-Type: application/json" \
    -d "{\"repository\": \"$INT_REPO_HREF\"}")

if echo "$INT_PUB_RESPONSE" | jq -e '.task' > /dev/null; then
    INT_PUB_TASK=$(echo "$INT_PUB_RESPONSE" | jq -r '.task')
    wait_for_task "$INT_PUB_TASK" "$PULP_PRIMARY"
    
    # Get publication href
    PUB_TASK_RESULT=$(curl -s -u $PULP_USER:$PULP_PASS "$PULP_PRIMARY$INT_PUB_TASK")
    INT_PUB_HREF=$(echo "$PUB_TASK_RESULT" | jq -r '.created_resources[0]')
    echo "  ✅ Internal publication created: $INT_PUB_HREF"
else
    INT_PUB_HREF=$(echo "$INT_PUB_RESPONSE" | jq -r '.pulp_href')
    echo "  ✅ Internal publication exists: $INT_PUB_HREF"
fi

echo ""
echo "Step 5: Creating Distributions"
echo "=============================="

# Create distribution for external repository
if check_exists "$PULP_PRIMARY/pulp/api/v3/distributions/deb/apt/" "ext-small-repo"; then
    echo "  ✅ Distribution 'ext-small-repo' already exists"
else
    echo "  🌐 Creating distribution for ext-small-repo..."
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
    echo "  ✅ External distribution created"
fi

# Create distribution for internal repository
if check_exists "$PULP_PRIMARY/pulp/api/v3/distributions/deb/apt/" "int-demo-packages"; then
    echo "  ✅ Distribution 'int-demo-packages' already exists"
else
    echo "  🌐 Creating distribution for int-demo-packages..."
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
    echo "  ✅ Internal distribution created"
fi

echo ""
echo "🎉 Demo Setup Complete!"
echo "======================"
echo ""
echo "Available repositories:"
echo "  📦 ext-small-repo (external): $PULP_PRIMARY/pulp/content/ext-small-repo/"
echo "  📦 int-demo-packages (internal): $PULP_PRIMARY/pulp/content/int-demo-packages/"
echo ""
echo "Next steps:"
echo "  1. Sync external repository: curl -u admin:password -X POST '$PULP_PRIMARY${EXT_REPO_HREF}sync/' -H 'Content-Type: application/json' -d '{\"remote\": \"$REMOTE_HREF\"}'"
echo "  2. Check sync status via Pulp Manager API"
echo "  3. Configure secondary server to sync from primary"
echo ""
echo "View repositories: curl -u admin:password '$PULP_PRIMARY/pulp/api/v3/repositories/deb/apt/' | jq '.results[] | {name, pulp_href}'"