"""Shared package paths, resolved from this checkout or installed copy."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIGS = ROOT / "configs"
DATA = ROOT / "data"
ENGINE = ROOT / "engine"
STATE = ROOT / "state"
SOUNDBAR = ROOT / "soundbar"
