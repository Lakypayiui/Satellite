"""Web console (FastAPI + static SPA)."""

from satellite.web.app import create_app, get_app

__all__ = ["create_app", "get_app"]
