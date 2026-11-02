import json
from types import SimpleNamespace

import pytest

from sopmaker.models import DraftBatch
from sopmaker.provider import OpenAIProvider, safe_error


def test_provider_constrains_citations_to_supplied_ids_and_disables_storage(monkeypatch, tmp_path):
    from PIL import Image

    Image.new("RGB", (20, 20), "white").save(tmp_path / "sample.jpg")
    captured = {}

    def respond(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status="completed", output_text='{"observations":[]}',
                               id="fixture-response", model="fixture-model", usage=None)

    provider = object.__new__(OpenAIProvider)
    provider.client = SimpleNamespace(responses=SimpleNamespace(create=respond))
    sources = {"real-frame-001": {"kind": "image", "file": "sample.jpg", "seconds": 1.2}}
    batch, record = provider.draft(sources, tmp_path, "fixture-model", "")
    assert isinstance(batch, DraftBatch) and not batch.observations
    assert captured["store"] is False
    schema = captured["text"]["format"]["schema"]
    assert schema["$defs"]["Evidence"]["properties"]["source_id"]["enum"] == ["real-frame-001"]
    assert record["model"] == "fixture-model"
    assert "api_key" not in json.dumps(record)


def test_unknown_exception_text_is_not_exposed():
    assert "invented-secret" not in safe_error(RuntimeError("invented-secret"))


def test_plaintext_credential_backend_is_rejected(monkeypatch):
    import keyring

    from sopmaker.credentials import Credentials

    monkeypatch.setattr(keyring, "get_keyring", lambda: SimpleNamespace())
    with pytest.raises(ValueError, match="Could not save"):
        Credentials(False).set("invented-fixture-key", remember=True)
