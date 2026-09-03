"""probe — detect which ATS a careers URL runs on, and emit a config.json entry."""

import requests  # re-exported so tests can patch probe.requests.Session

from probe.cli import _handle_one, main
from probe.config_io import CONFIG_PATH, _append_to_config, _format_entry, _verify
from probe.detect import (
    _augment_name,
    _build_config,
    _confirm_and_build,
    _guess_name,
    _host_matches,
    _path_slug,
    _slug_candidates,
    probe,
)
from probe.input_parse import _collect_entries, _extract_entries, _split_named
from probe.result import DetectionResult
from probe.signatures import (
    HOST_RULES,
    HTML_SIGNATURES,
    OTHER_ATS,
    OVERRIDE_FIELD,
    SUPPORTED_TYPES,
)

__all__ = ["probe", "main", "DetectionResult", "SUPPORTED_TYPES"]
