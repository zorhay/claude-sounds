"""Common fixtures for soundbar integration tests."""

import json
import sys
from pathlib import Path

import pytest

# Repo layout
REPO_ROOT = Path(__file__).resolve().parent.parent
SOUNDBAR_DIR = REPO_ROOT / "code-gossip"

# Direct script imports remain useful in unit tests; subprocess tests use entrypoints.
for directory in (SOUNDBAR_DIR, SOUNDBAR_DIR / "engine", SOUNDBAR_DIR / "hooks", SOUNDBAR_DIR / "soundbar"):
    sys.path.insert(0, str(directory))


@pytest.fixture
def repo_root():
    return REPO_ROOT


@pytest.fixture
def soundbar_dir():
    return SOUNDBAR_DIR


@pytest.fixture
def sounds_json():
    return json.loads((SOUNDBAR_DIR / "data/sounds.json").read_text())


@pytest.fixture
def config_defaults():
    return json.loads((SOUNDBAR_DIR / "configs/config.defaults.json").read_text())


@pytest.fixture
def claude_hooks_json():
    return (SOUNDBAR_DIR / "hooks/claude-hooks.json").read_text()


@pytest.fixture
def play_script():
    return (SOUNDBAR_DIR / "engine/play.sh").read_text()


@pytest.fixture
def switch_script():
    return (SOUNDBAR_DIR / "switch.sh").read_text()
