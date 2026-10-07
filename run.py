#!/usr/bin/env python3
"""Entry point for Pokemon Card Scanner."""
import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn
from api.main import app as api_app

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 5005))

    # Mount static files from src/static
    static_dir = os.path.join(os.path.dirname(__file__), "src", "static")
    if os.path.exists(static_dir):
        api_app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # Mount cached CLIP images locally
    clip_images_dir = os.path.join(os.path.dirname(__file__), "data", "clip_images")
    if os.path.exists(clip_images_dir):
        api_app.mount("/static/clip_images", StaticFiles(directory=clip_images_dir), name="clip_images")

    uvicorn.run(api_app, host=host, port=port, log_level="info")
