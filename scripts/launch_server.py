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

os.execv(
    "/opt/data/pokemon-card-scanner/.venv/bin/python",
    ["/opt/data/pokemon-card-scanner/.venv/bin/python", "/opt/data/pokemon-card-scanner/daemon.py"],
)
