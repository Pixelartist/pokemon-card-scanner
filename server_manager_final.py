#!/usr/bin/env python3
"""Robust server manager with proper process supervision."""
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

class ServerManager:
    def __init__(self):
        self.server_process = None
        self.restart_count = 0
        self.max_restarts = 50
        self.shutdown_requested = False
        
    def start_server(self):
        """Start the server process with proper supervision."""
        try:
            logger.info(f"Starting server process (attempt {self.restart_count + 1})")
            
            # Create a dedicated log file for this server instance
            instance_log = os.path.join(PROJECT_DIR, f"server_{int(time.time())}.log")
            
            self.server_process = subprocess.Popen(
                [os.path.join(PROJECT_DIR, ".venv", "bin", "python"), "run.py"],
                cwd=PROJECT_DIR,
                stdout=open(instance_log, "a"),
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid
            )
            
            logger.info(f"Server started with PID: {self.server_process.pid}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start server: {e}")
            return False
    
    def wait_for_server(self, timeout=30):
        """Wait for server to become healthy."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.server_process and self.server_process.poll() is None:
                # Server is running, try health check
                try:
                    import requests
                    response = requests.get(
                        "http://localhost:5005/health",
                        timeout=5
                    )
                    if response.status_code == 200:
                        logger.info("Server health check passed")
                        return True
                except:
                    pass
            time.sleep(0.5)
        
        logger.warning(f"Server failed to become healthy within {timeout} seconds")
        return False
    
    def monitor_server(self):
        """Monitor server process and restart if needed."""
        while not self.shutdown_requested:
            if self.server_process is None:
                logger.warning("Server process is None, restarting")
                break
            
            exit_code = self.server_process.poll()
            
            if exit_code is not None:
                self.restart_count += 1
                logger.info(f"Server exited with code {exit_code}, restart attempt {self.restart_count}")
                
                if self.restart_count >= self.max_restarts:
                    logger.error(f"Max restarts ({self.max_restarts}) reached, shutting down")
                    self.shutdown_requested = True
                    break
                
                # Check if process crashed (non-zero exit code that's not normal shutdown)
                if exit_code != 0:
                    logger.warning(f"Server crashed with exit code {exit_code}")
                
                time.sleep(2)  # Wait before restart
                self.start_server()
                self.wait_for_server()
            
            time.sleep(1)
    
    def stop(self):
        """Stop the server manager and all child processes."""
        logger.info("Stopping server manager...")
        self.shutdown_requested = True
        
        if self.server_process:
            logger.info("Terminating server process...")
            try:
                os.killpg(os.getpgid(self.server_process.pid), signal.SIGTERM)
                self.server_process.wait(timeout=10)
            except:
                logger.warning("Graceful termination failed, forcing kill")
                try:
                    os.killpg(os.getpgid(self.server_process.pid), signal.SIGKILL)
                except:
                    pass
        
        logger.info("Server manager stopped")

def main():
    manager = ServerManager()
    
    # Start initial server
    if not manager.start_server():
        logger.error("Failed to start initial server")
        sys.exit(1)
    
    if not manager.wait_for_server():
        logger.error("Server failed to become healthy")
        sys.exit(1)
    
    logger.info("Server manager started successfully")
    
    try:
        manager.monitor_server()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    finally:
        manager.stop()

if __name__ == "__main__":
    main()