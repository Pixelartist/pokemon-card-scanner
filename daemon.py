#!/usr/bin/env python3
"""Daemon entry point: detaches from the controlling terminal so it survives session end."""
import os
import sys

# Detach from controlling terminal — survives parent session exit.
# Already a session leader when launched via double-fork launcher.
try:
    os.setsid()
except OSError:
    pass
os.chdir('/opt/data/pokemon-card-scanner')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn
from api.main import app as api_app

host = os.environ.get("HOST", "0.0.0.0")
port = int(os.environ.get("PORT", 5005))

static_dir = os.path.join(os.path.dirname(__file__), "src", "static")
if os.path.exists(static_dir):
    api_app.mount("/static", StaticFiles(directory=static_dir), name="static")

clip_images_dir = os.path.join(os.path.dirname(__file__), "data", "clip_images")
if os.path.exists(clip_images_dir):
    api_app.mount("/static/clip_images", StaticFiles(directory=clip_images_dir), name="clip_images")

uvicorn.run(api_app, host=host, port=port, log_level="info")