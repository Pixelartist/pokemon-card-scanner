#!/bin/bash
cd /opt/data/pokemon-card-scanner

echo "=== Pokemon Card Scanner Server Manager ==="
echo

# Function to cleanup
echo "Cleaning up any existing processes..."
pkill -f "run.py" 2>/dev/null
pkill -f "quick_start.sh" 2>/dev/null
sleep 2

# Double-check port is free
if lsof -ti:5005 >/dev/null 2>&1; then
    echo "Port 5005 is still in use. Cleaning up..."
    lsof -ti:5005 | xargs kill -9 2>/dev/null
    sleep 2
fi

echo "Starting Pokemon Card Scanner server..."
echo

# Start the server
.venv/bin/python run.py > server.log 2>&1 &
SERVER_PID=$!
echo "Server started with PID: $SERVER_PID"
echo $SERVER_PID > server.pid

# Wait for server to start
echo "Waiting for server to start (health checking)..."
for i in {1..15}; do
    if curl -s http://localhost:5005/health >/dev/null 2>&1; then
        echo
        echo "✅ SUCCESS: Server is running and healthy!"
        echo "   PID: $SERVER_PID"
        echo "   Health endpoint: http://localhost:5005/health"
        echo "   External URL: https://pixelartist.myqnapcloud.com:679/"
        echo
        echo "Server is ready for use. Press Ctrl+C to stop."
        
        # Wait for server process
        wait $SERVER_PID
        break
    else
        if [ $i -eq 15 ]; then
            echo
            echo "❌ FAILED: Server did not start successfully after 15 attempts"
            echo "Check server.log for error details"
            exit 1
        else
            echo -n "   Attempt $i/15 - waiting..."
            sleep 2
            echo " (still waiting)"
        fi
    fi
done