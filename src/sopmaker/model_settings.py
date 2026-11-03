"""Curated choices; access and extraction accuracy still require user evaluation."""

DEFAULT_NOTES_MODEL = "gpt-6.1-sol"
MODEL_OPTIONS = [
    {"id": "gpt-6.1-sol", "label": "Recommended · GPT-6.1 Sol"},
    {"id": "gpt-6.1-terra", "label": "Lower cost · GPT-6.1 Terra"},
    {"id": "gpt-5.4-mini", "label": "Previous option · GPT-5.4 Mini"},
]


def draft_options(model):
    if model in ("gpt-6.1-sol", "gpt-6.1-terra"):
        return {"reasoning": {"effort": "low"}, "max_output_tokens": 12_000}
    return {"max_output_tokens": 6000}
