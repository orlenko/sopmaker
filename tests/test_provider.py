import json
from types import SimpleNamespace

import pytest

from sopmaker.models import DraftBatch
from sopmaker.provider import OpenAIProvider, evidence_schema, quote_choices, safe_error


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
    claim = schema["$defs"]["Claim"]["properties"]
    assert claim["basis"]["enum"] == ["visible_detail"]
    assert claim["evidence"]["items"]["properties"]["source_id"]["enum"] == ["real-frame-001"]
    assert claim["evidence"]["items"]["properties"]["quote"] == {"type": "null"}
    assert record["model"] == "fixture-model"
    assert "api_key" not in json.dumps(record)


def test_mixed_evidence_schema_separates_spoken_and_visual_claims():
    schema = evidence_schema({"photo": {"kind": "image"}, "speech": {"kind": "transcript", "text": "Invented spoken account."}})
    variants = schema["$defs"]["Claim"]["anyOf"]
    kinds = {}
    for variant in variants:
        claim = variant["properties"]
        reference = claim["evidence"]["items"]["properties"]
        kinds[claim["basis"]["enum"][0]] = reference
    assert kinds["visible_detail"]["source_id"]["enum"] == ["photo"]
    assert kinds["visible_detail"]["quote"]["type"] == "null"
    assert kinds["inspector_narration"]["source_id"]["enum"] == ["speech"]
    assert kinds["inspector_narration"]["quote"]["type"] == "string"
    assert kinds["inspector_narration"]["quote"]["enum"] == ["Invented spoken account."]


def test_transcript_only_and_empty_evidence_boundaries():
    claim = evidence_schema({"speech": {"kind": "transcript", "text": "Invented account."}})["$defs"]["Claim"]["properties"]
    assert claim["basis"]["enum"] == ["inspector_narration"]
    with pytest.raises(ValueError, match="No supported evidence"):
        evidence_schema({})


def test_quotes_are_bound_to_their_transcript_chunk():
    sources = {"first": {"kind": "transcript", "text": "First invented account."},
               "second": {"kind": "transcript", "text": "Second invented account."}}
    items = evidence_schema(sources)["$defs"]["Claim"]["properties"]["evidence"]["items"]["anyOf"]
    choices = {item["properties"]["source_id"]["enum"][0]: item["properties"]["quote"]["enum"] for item in items}
    assert choices == {"first": ["First invented account."], "second": ["Second invented account."]}


def test_quote_choices_preserve_raw_text_and_bound_dense_recordings():
    text = "An invented check.  Another check!\nA third detail? Final text without punctuation"
    assert quote_choices(text) == ["An invented check.", "Another check!", "A third detail?", "Final text without punctuation"]
    dense = "  ".join(f"Invented point {index}." for index in range(400))
    choices = quote_choices(dense)
    assert len(choices) <= 80
    assert all(choice in dense for choice in choices)
    with pytest.raises(ValueError, match="No supported evidence"):
        evidence_schema({"empty": {"kind": "transcript", "text": "   "}})


def test_unknown_exception_text_is_not_exposed():
    assert "invented-secret" not in safe_error(RuntimeError("invented-secret"))


def test_plaintext_credential_backend_is_rejected(monkeypatch):
    import keyring

    from sopmaker.credentials import Credentials

    monkeypatch.setattr(keyring, "get_keyring", lambda: SimpleNamespace())
    with pytest.raises(ValueError, match="Could not save"):
        Credentials(False).set("invented-fixture-key", remember=True)
