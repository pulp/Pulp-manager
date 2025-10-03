#!/bin/bash
# Wrapper script that runs Pulp's normal entrypoint, then sets up signing service

# Start Pulp in the background using its normal entrypoint
/usr/local/bin/pulp-api &
PULP_PID=$!

# Wait for Pulp to be ready
echo "Waiting for Pulp to start..."
for i in $(seq 1 60); do
    if curl -s http://localhost/pulp/api/v3/status/ > /dev/null 2>&1; then
        echo "Pulp is ready"
        break
    fi
    sleep 2
done

# Run signing service setup
/opt/scripts/setup-signing-service.sh

# Wait for Pulp process
wait $PULP_PID
