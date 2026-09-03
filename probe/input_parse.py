"""Pulling (name, url) pairs out of CLI args and --file content."""

import re
import sys
from pathlib import Path


def _split_named(line):
    """Parse `Display Name, https://url` (comma, tab, or 2+ spaces as separator).
    Returns (name, url) or (None, None). Rejects a "name" that is itself a URL."""
    m = re.match(r'\s*(?:[-*>]\s+)?(.+?)(?:\s*,\s+|\t+| {2,})\s*(https?://\S+)\s*$', line)
    if not m:
        return None, None
    name = m.group(1).strip().strip('"\'')
    if not name or re.match(r'https?://', name):
        return None, None
    return name, m.group(2).rstrip(".,;")


def _extract_entries(text):
    """Pull (name, url) pairs from arbitrary text. Strips HTML comments and
    # heading lines; deduplicates URLs, preserves order."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    out, seen = [], set()
    for ln in text.splitlines():
        if ln.lstrip().startswith("#"):
            continue
        name, url = _split_named(ln)
        if url:
            pairs = [(name, url)]
        else:
            pairs = [(None, u.rstrip(".,;"))
                     for u in re.findall(r'https?://[^\s)\]>"\'`]+', ln)]
        for nm, u in pairs:
            if u and u not in seen:
                seen.add(u)
                out.append((nm, u))
    return out


def _collect_entries(args):
    """Gather (name, url) pairs from positional args and/or --file."""
    text = "\n".join(args.urls)
    if args.file:
        text += "\n" + (sys.stdin.read() if args.file == "-" else Path(args.file).read_text())
    return _extract_entries(text)
