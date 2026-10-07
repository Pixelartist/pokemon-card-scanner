#!/usr/bin/env python3
"""Persistent server manager with proper process supervision."""
import subprocess
import time
import sys
import os
import signal

PROJECT_DIR = "/opt/data/pokemon-card-scanner"
LOG_FILE = os.path.join(PROJECT_DIR, "server.log")

def main():
    print(f"Starting persistent server manager at {time.ctime()}")
    sys.stdout.flush()
    
    restart_count = 0
    max_restarts = 100
    
    while restart_count < max_restarts:
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
    
    print(f"[{time.ctime()}] Max restarts ({max_restarts}) reached, exiting")
    sys.stdout.flush()

if __name__ == "__main__":
    main()