#!/bin/bash

# Deb signing script that calls the signing service
# $1 = input file, $2 = output file

# Check if input file exists
if [ ! -f "$1" ]; then
    echo '{"error": "Input file not found", "status": "failed"}'
    exit 1
fi

# Set default output file if not provided
if [ -z "$2" ]; then
    OUTPUT_FILE="$1.asc"
else
    OUTPUT_FILE="$2"
fi

# Call the signing service
RESPONSE=$(curl -s -F "file=@$1" http://deb-signing-service:8080/sign)

# Extract signature content from response
SIGNATURE_CONTENT=$(echo "$RESPONSE" | python3 -c "import json, sys; data=json.load(sys.stdin); print(data.get('signature_content', ''))")
STATUS=$(echo "$RESPONSE" | python3 -c "import json, sys; data=json.load(sys.stdin); print(data.get('status', ''))")

if [ "$STATUS" = "success" ] && [ -n "$SIGNATURE_CONTENT" ]; then
    # Write signature content to output file (using printf to handle \n correctly)
    printf "%s" "$SIGNATURE_CONTENT" > "$OUTPUT_FILE"
    # Return JSON with the correct signature file path for Pulp
    KEY_ID=$(echo "$RESPONSE" | python3 -c "import json, sys; data=json.load(sys.stdin); print(data.get('key_id', ''))")
    echo "{\"signature\": \"$OUTPUT_FILE\", \"status\": \"success\", \"key_id\": \"$KEY_ID\"}"
else
    # Fallback: copy input to output and return error
    cp "$1" "$OUTPUT_FILE"
    echo '{"error": "Signing service failed", "status": "failed"}'
    exit 1
fi