#!/usr/bin/env python3
"""Double-fork + os.setsid() launcher so the server survives Hermes tool-call teardowns.

Every interrupted terminal() call sends SIGHUP to its entire process group,
killing background processes launched with & inside that call. This launcher
puts the worker in its own session detached from Hermes entirely.
Stdout/stderr are redirected to a log file so the daemon never writes to a
pipe that closes when this launcher exits.
"""
import os
import sys

LOG = "/tmp/daemon.log"

if os.fork() != 0:
    os._exit(0)
os.setsid()
if os.fork() != 0:
    os._exit(0)

log_fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
os.dup2(log_fd, 1)
os.dup2(log_fd, 2)
devnull = os.open(os.devnull, os.O_RDONLY)
os.dup2(devnull, 0)

# Run the daemon with the interpreter that started this launcher, from the
# checkout this script lives in.
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.execv(sys.executable, [sys.executable, os.path.join(BASE, "daemon.py")])
