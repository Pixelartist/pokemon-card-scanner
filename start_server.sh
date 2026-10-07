#!/bin/bash
# Pokemon Card Scanner Independent Server Startup Script
# 
# This script provides an easy way to start, restart, and manage the Pokemon Card Scanner
# without Hermes process dependencies. It handles cache-busting and virtual environment isolation.

PROJECT_DIR="/opt/data/pokemon-card-scanner"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
RUN_PY="$PROJECT_DIR/run.py"

# Simple output functions (avoiding complex color codes that cause syntax errors)
print_red() { echo "[ERROR] $1"; }
print_green() { echo "[OK] $1"; }
print_yellow() { echo "[WARN] $1"; }
print_blue() { echo "[INFO] $1"; }

# Function to check if virtual environment exists
check_venv() {
    if [[ ! -f "$VENV_PYTHON" ]]; then
        print_red "Virtual environment not found at $VENV_PYTHON"
        echo "Please ensure the .venv directory exists in $PROJECT_DIR"
        return 1
    fi
    
    # Test if Python works
    if ! $VENV_PYTHON -c "import sys; print('OK')" >/dev/null 2>&1; then
        print_red "Virtual environment Python is not working"
        return 1
    fi
    
    print_green "Virtual environment ready"
    return 0
}

# Function to check if run.py exists
check_run_py() {
    if [[ ! -f "$RUN_PY" ]]; then
        print_red "run.py not found at $RUN_PY"
        return 1
    fi
    
    print_green "run.py found"
    return 0
}

# Function to kill existing processes
kill_existing() {
    print_yellow "Stopping any existing server processes..."
    
    # Kill by filename
    pkill -f "run.py" 2>/dev/null || true
    pkill -f "main.py" 2>/dev/null || true
    
    # Wait a bit for processes to terminate
    sleep 2
    
    # Check if any are still running
    if pgrep -f "run.py" >/dev/null 2>&1 || pgrep -f "main.py" >/dev/null 2>&1; then
        print_red "Warning: Some processes may still be running"
    else
        print_green "No conflicting processes found"
    fi
}

# Function to start the server
start_server() {
    print_blue "🚀 Starting Pokemon Card Scanner server..."
    print_blue "   URL: http://localhost:5005/"
    print_blue "   Press Ctrl+C to stop"
    print_blue "----------------------------------------"
    
    # Clear any existing logs
    > "$PROJECT_DIR/server.log"
    
    # Start the server
    $VENV_PYTHON $RUN_PY
}

# Function to check server status
check_server_status() {
    print_yellow "=== Server Status Check ==="
    
    # Try to connect
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:5005/health 2>/dev/null | grep -q "200"; then
        print_green "Health endpoint: OK"
        
        # Get homepage (first 5000 chars)
        html=$(curl -s http://localhost:5005/ 2>/dev/null | head -c 5000)
        
        # Check for key elements
        checks=(
            "login-modal:login-modal"
            "user-status:user-status" 
            "drop-zone:drop-zone"
            "cache-busting:?t="
        )
        
        print_blue "Homepage Structure:"
        for check in "${checks[@]}"; do
            local element=$(echo $check | cut -d: -f1)
            local pattern=$(echo $check | cut -d: -f2)
            
            if [[ $html == *"$pattern"* ]]; then
                print_green "  ✓ $element"
            else
                print_red "  ✗ $element (missing)"
            fi
        done
        
        return 0
    else
        print_red "Server is not running"
        return 1
    fi
}

# Function to show help
show_help() {
    print_blue "Pokemon Card Scanner Server Management"
    print_blue "======================================"
    echo ""
    echo "Usage: $0 <command>"
    echo ""
    echo "Commands:"
    echo "  start       Start the server (default)"
    echo "  stop        Stop any running server"
    echo "  restart     Restart the server"
    echo "  status      Check server status"
    echo "  help        Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 start     # Start the server"
    echo "  $0 stop      # Stop the server"
    echo "  $0 restart   # Restart the server"
    echo "  $0 status    # Check if server is running"
    echo ""
    echo "The server will be accessible at: http://localhost:5005/"
    echo "All fixes are applied (cache-busting, auth headers, modal fixes, etc.)"
}

# Function to stop server
stop_server() {
    print_yellow "Stopping server..."
    
    kill_existing
    
    if pgrep -f "run.py" >/dev/null 2>&1 || pgrep -f "main.py" >/dev/null 2>&1; then
        print_red "Warning: Server still running after stop attempt"
    else
        print_green "Server stopped"
    fi
}

# Main script logic
case "$1" in
    start|run)
        kill_existing
        check_venv && check_run_py
        start_server
        ;;
        
    stop)
        stop_server
        ;;
        
    restart)
        stop_server
        sleep 3
        check_venv && check_run_py
        start_server
        ;;
        
    status)
        check_server_status
        ;;
        
    help|--help|-h)
        show_help
        ;;
        
    *)
        echo -e "Unknown command: $1"
        echo "Run '$0 help' for usage information."
        exit 1
        ;;
esac

# Function to kill existing processes
kill_existing() {
    echo -e "${YELLOW}Stopping any existing server processes...${NC}"
    
    # Kill by filename
    pkill -f "run.py" 2>/dev/null || true
    pkill -f "main.py" 2>/dev/null || true
    
    # Wait a bit for processes to terminate
    sleep 2
    
    # Check if any are still running
    if pgrep -f "run.py" >/dev/null 2>&1 || pgrep -f "main.py" >/dev/null 2>&1; then
        echo -e "${RED}Warning: Some processes may still be running${NC}"
    else
        print_status "$GREEN" "No conflicting processes found"
    fi
}

# Function to start the server
start_server() {
    echo -e "${BLUE}🚀 Starting Pokemon Card Scanner server...${NC}"
    echo -e "${BLUE}   URL: http://localhost:5005/${NC}"
    echo -e "${BLUE}   Press Ctrl+C to stop${NC}"
    echo -e "${BLUE}"----------------------------------------${NC}"
    
    # Clear any existing logs
    > "$PROJECT_DIR/server.log"
    
    # Start the server
    $VENV_PYTHON $RUN_PY
}

# Function to check server status
check_server_status() {
    echo -e "${YELLOW}=== Server Status Check ===${NC}"
    
    # Try to connect
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:5005/health | grep -q "200"; then
        print_status "$GREEN" "Health endpoint: OK"
        
        # Get homepage
        html=$(curl -s http://localhost:5005/ | head -c 5000)
        
        # Check for key elements
        checks=(
            "login-modal:login-modal"
            "user-status:user-status" 
            "drop-zone:drop-zone"
            "cache-busting:?t="
        )
        
        echo -e "${BLUE}✓ Homepage Structure:${NC}"
        for check in "${checks[@]}"; do
            local element=$(echo $check | cut -d: -f1)
            local pattern=$(echo $check | cut -d: -f2)
            
            if [[ $html == *"$pattern"* ]]; then
                print_status "$GREEN" "  ✓ $element"
            else
                echo -e "${RED}  ✗ $element (missing)${NC}"
            fi
        done
        
        return 0
    else
        echo -e "${RED}✗ Server is not running${NC}"
        return 1
    fi
}

# Function to show help
show_help() {
    echo -e "${BLUE}Pokemon Card Scanner Server Management${NC}"
    echo -e "${BLUE}======================================${NC}"
    echo ""
    echo "Usage: $0 <command>"
    echo ""
    echo "Commands:"
    echo "  start       Start the server (default)"
    echo "  stop        Stop any running server"
    echo "  restart     Restart the server"
    echo "  status      Check server status"
    echo "  help        Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 start     # Start the server"
    echo "  $0 stop      # Stop the server"
    echo "  $0 restart   # Restart the server"
    echo "  $0 status    # Check if server is running"
    echo ""
    echo "The server will be accessible at: http://localhost:5005/"
    echo "All fixes are applied (cache-busting, auth headers, etc.)"
}

# Function to stop server
stop_server() {
    echo -e "${YELLOW}Stopping server...${NC}"
    
    kill_existing
    
    if pgrep -f "run.py" >/dev/null 2>&1 || pgrep -f "main.py" >/dev/null 2>&1; then
        echo -e "${RED}Warning: Server still running after stop attempt${NC}"
    else
        print_status "$GREEN" "Server stopped"
    fi
}

# Main script logic
case "$1" in
    start|run)
        kill_existing
        check_venv && check_run_py
        start_server
        ;;
        
    stop)
        stop_server
        ;;
        
    restart)
        stop_server
        sleep 3
        check_venv && check_run_py
        start_server
        ;;
        
    status)
        check_server_status
        ;;
        
    help|--help|-h)
        show_help
        ;;
        
    *)
        echo -e "${YELLOW}Unknown command: $1${NC}"
        echo "Run '$0 help' for usage information."
        exit 1
        ;;
esac