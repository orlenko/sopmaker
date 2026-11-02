from pathlib import Path

import pytest
from PIL import Image

from sopmaker.store import Store


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "PYTHON_KEYRING_BACKEND"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "workspace")


@pytest.fixture
def photo(tmp_path) -> Path:
    file = tmp_path / "synthetic-label.png"
    Image.new("RGB", (160, 120), "green").save(file)
    return file
