#!/bin/bash
cd /opt/data/pokemon-card-scanner

echo "=== Pokemon Card Scanner - Simple Direct Server ==="
echo

# Clean up
echo "Stopping any existing server processes..."
pkill -f "run.py" 2>/dev/null
pkill -f "final_start.sh" 2>/dev/null
sleep 2

# Double-check port is free
if lsof -ti:5005 >/dev/null 2>&1; then
    echo "Port 5005 is still in use, cleaning up..."
    lsof -ti:5005 | xargs kill -9 2>/dev/null
    sleep 2
fi

echo "Starting Pokemon Card Scanner server directly..."
.venv/bin/python run.py > server.log 2>&1 &
echo $! > server.pid

echo "Server started. Waiting for it to be ready..."
echo

# Wait for server with health check
for i in {1..20}; do
    if curl -s http://localhost:5005/health >/dev/null 2>&1; then
        echo "✅ SUCCESS: Server is running and healthy!"
        echo "   PID: $!"
        echo "   Server is ready. Press Ctrl+C to stop."
        echo
        echo "You can now use the server. Test commands:"
        echo "  curl http://localhost:5005/health"
        echo "  curl -X POST -F 'image=@/path/to/image.jpg' http://localhost:5005/api/scan"
        echo "  curl -X POST -F 'card_id=1' http://localhost:5005/api/collection/add"
        echo
        echo "Server logs are available in: server.log"
        echo
        
        # Keep running
        wait $!
        break
    else
        if [ $i -eq 20 ]; then
            echo "❌ FAILED: Server did not start successfully after 20 attempts"
            echo "Check server.log for error details"
            exit 1
        else
            echo -n "   Attempt $i/20 - waiting..."
            sleep 2
            echo " (still waiting)"
        fi
    fi
done