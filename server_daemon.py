#!/usr/bin/env python3
"""Daemon server manager using double-fork for proper detachment."""
import subprocess
import time
import sys
import os

PROJECT_DIR = "/opt/data/pokemon-card-scanner"
LOG_FILE = os.path.join(PROJECT_DIR, "server.log")

def daemonize():
    """Double-fork to detach from parent."""
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    
    # Redirect stdout/stderr to log
    sys.stdout = open(LOG_FILE, "a")
    sys.stderr = sys.stdout
    
    print(f"[{time.ctime()}] Daemon started")
    
    restart_count = 0
    while restart_count < 1000:
        restart_count += 1
        print(f"[{time.ctime()}] Starting server (attempt {restart_count})")
        sys.stdout.flush()
        
        try:
            proc = subprocess.Popen(
                [os.path.join(PROJECT_DIR, ".venv", "bin", "python"), "run.py"],
                cwd=PROJECT_DIR,
                stdout=open(LOG_FILE, "a"),
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid
            )
            print(f"[{time.ctime()}] Server PID: {proc.pid}")
            sys.stdout.flush()
            
            exit_code = proc.wait()
            print(f"[{time.ctime()}] Server exited with code {exit_code}, restarting in 2s...")
            sys.stdout.flush()
            time.sleep(2)
        except Exception as e:
            print(f"[{time.ctime()}] Error: {e}")
            sys.stdout.flush()
            time.sleep(2)
    
    print(f"[{time.ctime()}] Max restarts reached")
    sys.stdout.flush()

if __name__ == "__main__":
    daemonize()