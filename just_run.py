#!/usr/bin/env python3
"""Simple direct server starter."""
import os
import sys
import subprocess
import time
import signal

PROJECT_DIR = "/opt/data/pokemon-card-scanner"

def main():
    # Kill any existing processes
    print("Stopping any existing server processes...")
    for pid in os.popen("ps aux | grep 'run.py' | grep -v grep | awk '{print $2}'").read().strip().split():
        if pid:
            try:
                os.kill(int(pid), signal.SIGKILL)
                print(f"Killed process {pid}")
            except:
                pass
    
    # Wait a bit
    time.sleep(1)
    
    # Double-check port is free
    print("Checking port availability...")
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('localhost', 5005))
        sock.close()
        if result == 0:
            print("Port 5005 is still in use!")
            return
    except:
        pass
    
    print("Starting Pokemon Card Scanner server...")
    
    try:
        # Start the server with direct approach
        server_proc = subprocess.Popen(
            [os.path.join(PROJECT_DIR, ".venv", "bin", "python"), "run.py"],
            cwd=PROJECT_DIR,
            stdout=open(os.path.join(PROJECT_DIR, "server.log"), "a"),
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid
        )
        
        print(f"Server started with PID: {server_proc.pid}")
        
        # Give it time to start
        time.sleep(3)
        
        # Test health check
        import requests
        for i in range(10):
            try:
                response = requests.get("http://localhost:5005/health", timeout=3)
                if response.status_code == 200:
                    print("✅ Server is running!")
                    print(f"   Health: {response.json()}")
                    print("\nServer is ready. Press Ctrl+C to stop.")
                    
                    # Keep running
                    try:
                        server_proc.wait()
                    except KeyboardInterrupt:
                        print("\nStopping server...")
                        break
                    return
            except:
                if i < 9:
                    time.sleep(1)
                    print(f"   Waiting for server to start... ({i+1}/10)")
        
        print("❌ Server failed to start or health check failed")
        
    except Exception as e:
        print(f"❌ Failed to start server: {e}")
        # Cleanup
        try:
            os.killpg(os.getpgid(server_proc.pid), signal.SIGTERM)
        except:
            pass

if __name__ == "__main__":
    main()