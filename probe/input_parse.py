"""Pulling (name, url) pairs out of CLI args and --file content.

Accepts Markdown lists, prose, and a pinned `Display Name, https://url` form (also
tab- or 2+-space-separated) so a pasted `Name<TAB>url` list drops straight in.
"""

import re
import sys
from pathlib import Path


def _split_named(line):
    """Parse the explicit `Display Name <sep> https://url` form, where <sep> is a
    comma+space (`Acme, https://...`), a tab, or two-or-more spaces — so a pasted
    `Name<TAB>url` / `Name   url` list pins the name just like the comma form does.
    Returns (name, url), else (None, None) so the caller falls back to plain URL
    extraction. A leading Markdown bullet/quote is stripped, and a "name" that is
    itself a URL is rejected (so a bare line, a single-space prose line, or one
    with several URLs isn't misread as named)."""
    m = re.match(r'\s*(?:[-*>]\s+)?(.+?)(?:\s*,\s+|\t+| {2,})\s*(https?://\S+)\s*$', line)
    if not m:
        return None, None
    name = m.group(1).strip().strip('"\'')
    if not name or re.match(r'https?://', name):
        return None, None
    return name, m.group(2).rstrip(".,;")


def _extract_entries(text):
    """Pull (name, url) pairs out of arbitrary text, one logical entry per line.
    A line may be a plain URL — Markdown (`- [Name](url)`, bullets, tables) or
    prose, where name is None and gets guessed later — or the explicit
    `Display Name, https://url` form, which pins the config entry's name so probe
    never has to guess it. HTML comments (`<!-- ... -->`) and `#` heading/comment
    lines are dropped; duplicate URLs removed, order preserved."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    out, seen = [], set()
    for ln in text.splitlines():
        if ln.lstrip().startswith("#"):
            continue
        name, url = _split_named(ln)
        if url:
            pairs = [(name, url)]
        else:  # no Name, URL shape — pull every bare URL out of the line
            pairs = [(None, u.rstrip(".,;"))
                     for u in re.findall(r'https?://[^\s)\]>"\'`]+', ln)]
        for nm, u in pairs:
            if u and u not in seen:
                seen.add(u)
                out.append((nm, u))
    return out


def _collect_entries(args):
    """Gather (name, url) pairs from positional args and/or --file. Positionals
    are treated as lines too, so `probe.py "Acme, https://..."` works the same as
    a file line. Returns a list of (name-or-None, url)."""
    text = "\n".join(args.urls)
    if args.file:
        text += "\n" + (sys.stdin.read() if args.file == "-" else Path(args.file).read_text())
    return _extract_entries(text)
