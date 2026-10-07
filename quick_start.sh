#!/bin/bash
cd /opt/data/pokemon-card-scanner
# Clean up any existing processes
echo "Stopping any existing server..."
pkill -f "run.py" 2>/dev/null
pkill -f "just_run.py" 2>/dev/null
sleep 2
# Ensure port is free
if lsof -ti:5005 >/dev/null 2>&1; then
    echo "Port 5005 still in use, killing processes..."
    lsof -ti:5005 | xargs kill -9
    sleep 1
fi
# Start the server
echo "Starting Pokemon Card Scanner server..."
.venv/bin/python run.py > server.log 2>&1 &
echo $! > server.pid
sleep 5
# Wait for server to start
echo "Checking server health..."
for i in {1..10}; do
    if curl -s http://localhost:5005/health >/dev/null 2>&1; then
        echo "✅ Server is running successfully!"
        echo "PID: $!"
        echo "Server is ready. Press Ctrl+C to stop."
        wait $!
        break
    else
        if [ $i -eq 10 ]; then
            echo "❌ Server failed to start after 10 attempts"
            exit 1
        else
            echo "   Attempt $i/10 - waiting..."
            sleep 2
        fi
    fi
done