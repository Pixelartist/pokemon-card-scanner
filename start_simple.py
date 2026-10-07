#!/usr/bin/env python3
"""Simple server starter that works reliably."""
import subprocess
import time
import sys
import os
import signal
import logging

PROJECT_DIR = "/opt/data/pokemon-card-scanner"
LOG_FILE = os.path.join(PROJECT_DIR, "server.log")

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def main():
    logger.info("Starting Pokemon Card Scanner server...")
    
    # Clean up any existing processes
    logger.info("Cleaning up existing processes...")
    for pid in os.popen("ps aux | grep 'run.py' | grep -v grep | awk '{print $2}'").read().strip().split():
        if pid:
            try:
                os.kill(int(pid), signal.SIGKILL)
                logger.info(f"Killed process {pid}")
            except:
                pass
    
    # Also kill server_manager processes
    for pid in os.popen("ps aux | grep 'server_manager' | grep -v grep | awk '{print $2}'").read().strip().split():
        if pid:
            try:
                os.kill(int(pid), signal.SIGKILL)
                logger.info(f"Killed process {pid}")
            except:
                pass
    
    # Wait for processes to die
    time.sleep(2)
    
    # Double-check port is free
    try:
        test_proc = subprocess.Popen(
            ["nc", "-z", "localhost", "5005"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        test_proc.wait()
        if test_proc.returncode == 0:
            logger.error("Port 5005 is still in use, cannot start server")
            return
    except:
        pass
    
    # Start the server
    try:
        logger.info("Starting server process...")
        server_proc = subprocess.Popen(
            [os.path.join(PROJECT_DIR, ".venv", "bin", "python"), "run.py"],
            cwd=PROJECT_DIR,
            stdout=open(os.path.join(PROJECT_DIR, "server.log"), "a"),
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid
        )
        
        logger.info(f"Server started with PID: {server_proc.pid}")
        
        # Wait a bit for server to start
        time.sleep(3)
        
        # Try health check
        for i in range(10):  # Try 10 times
            try:
                import requests
                response = requests.get(
                    "http://localhost:5005/health",
                    timeout=5
                )
                if response.status_code == 200:
                    logger.info("Server health check passed!")
                    logger.info("Server is ready. Press Ctrl+C to stop.")
                    
                    # Keep the process running
                    try:
                        server_proc.wait()
                    except KeyboardInterrupt:
                        logger.info("Stopping server...")
                        break
                    return
            except:
                time.sleep(1)
        
        logger.error("Server failed to start or health check failed")
        
    except Exception as e:
        logger.error(f"Failed to start server: {e}")
        # Cleanup
        try:
            os.killpg(os.getpgid(server_proc.pid), signal.SIGTERM)
        except:
            pass

if __name__ == "__main__":
    main()