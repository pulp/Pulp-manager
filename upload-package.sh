#!/bin/bash

set -e

echo "Step 1: Getting repository 'internal-demo-packages'..."

# Try to create repository, if it exists, get the existing one
REPO_CREATE_RESPONSE=$(curl -s -u admin:password -X POST http://localhost:8000/pulp/api/v3/repositories/deb/apt/ \
  -H "Content-Type: application/json" \
  -d '{"name": "internal-demo-packages"}' 2>/dev/null) 

REPO_HREF=$(echo "$REPO_CREATE_RESPONSE" | jq -r '.pulp_href // empty')

if [ -z "$REPO_HREF" ]; then
    echo "Repository already exists, fetching it..."
    REPO_LIST_RESPONSE=$(curl -s -u admin:password "http://localhost:8000/pulp/api/v3/repositories/deb/apt/?name=internal-demo-packages")
    REPO_HREF=$(echo "$REPO_LIST_RESPONSE" | jq -r '.results[0].pulp_href')
fi

echo "Repository: $REPO_HREF"

echo "Step 2: Uploading package content..."
CONTENT_RESPONSE=$(curl -s -u admin:password -X POST http://localhost:8000/pulp/api/v3/content/deb/packages/ \
  -H "Content-Type: multipart/form-data" \
  -F "file=@assets/packages/hello_2.10-2_amd64.deb")

# Get task href and check status
TASK_HREF=$(echo "$CONTENT_RESPONSE" | jq -r '.task')
echo "Checking upload task status..."

# Get task result (using full URL to ensure it works)
TASK_RESULT=$(curl -s -u admin:password "http://localhost:8000$TASK_HREF")
TASK_STATUS=$(echo "$TASK_RESULT" | jq -r '.state')

if [ "$TASK_STATUS" = "failed" ]; then
    echo "Content upload task failed!"
    echo "Error: $(echo "$TASK_RESULT" | jq -r '.error.description // .error')"
    exit 1
fi

# Get content href from task result
CONTENT_HREF=$(echo "$TASK_RESULT" | jq -r '.created_resources[0]')
echo "Content created: $CONTENT_HREF"

echo "Step 3: Adding content to repository..."
MODIFY_RESPONSE=$(curl -s -u admin:password -X POST "http://localhost:8000${REPO_HREF}modify/" \
  -H "Content-Type: application/json" \
  -d "{\"add_content_units\": [\"$CONTENT_HREF\"]}")

MODIFY_TASK=$(echo "$MODIFY_RESPONSE" | jq -r '.task')
echo "Repository modification task: $MODIFY_TASK"

echo "✅ Demo package uploaded and added to repository successfully!"