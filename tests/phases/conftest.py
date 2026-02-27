"""Shared fixtures for phase tests."""

from __future__ import annotations

from pathlib import Path

from src.utils.config import load_config

SOL_CONFIG = load_config(Path(__file__).resolve().parents[2] / "config" / "sol.yaml")
