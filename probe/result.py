"""Probe detection result dataclass."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DetectionResult:
    type: str | None = None            # e.g. "workday", or None
    confidence: str | None = None      # "high" / "medium" / "low"
    evidence: str = ""
    note: str = ""                     # page-fetch status note
    slug: str | None = None            # company slug/token
    job_count: int | None = None       # from active API confirmation
    other: str | None = None           # recognized-but-unsupported ATS
    name: str | None = None            # display name from caller
    api_name: str | None = None        # display name from ATS API
    config: dict | None = None         # ready-to-paste config entry
