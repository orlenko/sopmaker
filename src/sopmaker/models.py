"""Provider output types and evidence validation, independent of UI and storage."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(StrictModel):
    source_id: str
    quote: str | None


class Claim(StrictModel):
    text: str
    basis: Literal["inspector_narration", "visible_detail"]
    evidence: list[Evidence] = Field(min_length=1)


class Draft(StrictModel):
    title: str
    area: str | None
    equipment_identifiers: list[str]
    claims: list[Claim] = Field(min_length=1)
    follow_ups: list[str]
    open_questions: list[str]
    tags: list[str]


class DraftBatch(StrictModel):
    observations: list[Draft]


def validate_evidence(batch: DraftBatch, sources: dict[str, dict]) -> None:
    """Constrain source existence/type and exact quotes; factual review remains human."""
    for draft in batch.observations:
        if not draft.title.strip() or len(draft.title) > 300:
            raise ValueError("The model returned an invalid observation title.")
        for claim in draft.claims:
            for reference in claim.evidence:
                source = sources.get(reference.source_id)
                if source is None:
                    raise ValueError("The model cited an unknown source.")
                expected = "transcript" if claim.basis == "inspector_narration" else "image"
                if source["kind"] != expected:
                    raise ValueError("The model cited an incompatible source type.")
                if claim.basis == "inspector_narration" and (not reference.quote or not reference.quote.strip()):
                    raise ValueError("A narration claim needs a supporting transcript quote.")
                if reference.quote is not None:
                    if source["kind"] != "transcript" or reference.quote not in source["text"]:
                        raise ValueError("The model's quote is absent from the transcript.")


def note_text(draft: dict) -> str:
    """Compose the editable draft from individually cited claims, without a second summary."""
    return "\n".join(claim["text"] for claim in draft["claims"])
