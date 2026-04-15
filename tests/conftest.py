"""Common fixtures for soundbar integration tests."""

import json
import sys
from pathlib import Path

import pytest

# Repo layout
REPO_ROOT = Path(__file__).resolve().parent.parent
SOUNDBAR_DIR = REPO_ROOT / "soundbar"

# Add soundbar/ to sys.path so we can import server and narrate
if str(SOUNDBAR_DIR) not in sys.path:
    sys.path.insert(0, str(SOUNDBAR_DIR))


@pytest.fixture
def repo_root():
    return REPO_ROOT


@pytest.fixture
def soundbar_dir():
    return SOUNDBAR_DIR


@pytest.fixture
def sounds_json():
    return json.loads((SOUNDBAR_DIR / "sounds.json").read_text())


@pytest.fixture
def config_defaults():
    return json.loads((SOUNDBAR_DIR / "config.defaults.json").read_text())


@pytest.fixture
def install_script():
    return (REPO_ROOT / "install.sh").read_text()


@pytest.fixture
def play_script():
    return (SOUNDBAR_DIR / "play.sh").read_text()


@pytest.fixture
def switch_script():
    return (SOUNDBAR_DIR / "switch.sh").read_text()
