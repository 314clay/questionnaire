import re
from typing import Any

from pydantic import BaseModel, field_validator

VALID_TYPES = {
    "multiple-choice", "multi-select", "confirm", "rich-choice",
    "toggle", "hold-button", "multi-live", "button-grid", "combo",
    "live-stream",
}

ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,62}[a-zA-Z0-9]$")


class CreateRequest(BaseModel):
    type: str
    payload: dict[str, Any]
    id: str | None = None
    allow_multiple: bool | None = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in VALID_TYPES:
            raise ValueError(f"type must be one of: {', '.join(sorted(VALID_TYPES))}")
        return v

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str | None) -> str | None:
        if v is not None and not ID_PATTERN.match(v):
            raise ValueError("id must be 3-64 chars, alphanumeric/hyphens/underscores")
        return v

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, v: dict) -> dict:
        if "question" not in v and "steps" not in v:
            raise ValueError("payload must contain 'question' or 'steps'")
        return v


class AudioClip(BaseModel):
    base64: str
    mimeType: str = "audio/webm"
    duration: int | None = None


class RespondRequest(BaseModel):
    audio: list[AudioClip] | None = None

    class Config:
        extra = "allow"
