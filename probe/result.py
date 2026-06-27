"""The result of probing one URL.

Replaces the stringly-typed `res` dict that used to be threaded through detection.
The fields are the union of everything the old dict could carry; the detection path
fills in what applies and leaves the rest at their defaults.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DetectionResult:
    # The detected supported type (e.g. "workday"), or None when nothing matched.
    type: str | None = None
    # "high" (host match) / "medium" (embedded signature) / "low" (guessed slug).
    confidence: str | None = None
    # Human-readable explanation of how the type was determined.
    evidence: str = ""
    # The page-fetch note (HTTP status + byte count, or a fetch-failure message).
    note: str = ""
    # The company slug/token (GH/Lever/Ashby), when one was found or confirmed.
    slug: str | None = None
    # Job count from an active API confirmation (None when not confirmed).
    job_count: int | None = None
    # A recognized-but-unsupported ATS name (no adapter), when type is None.
    other: str | None = None
    # A display name pinned by the caller (inline 'Name, url' or --name).
    name: str | None = None
    # A display name resolved from the ATS API (e.g. Greenhouse board name).
    api_name: str | None = None
    # The ready-to-paste config.json board entry (built once a type is known).
    config: dict | None = None
