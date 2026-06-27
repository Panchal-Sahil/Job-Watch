"""Reading from and writing to config.json.

_append_to_config does careful text surgery (rather than a json round-trip) to
preserve config.json's hand-formatted 2-line-per-board style while inserting a new
board into its ATS-type group. _verify runs the board through the real jobwatch
fetch path so we only ever add boards that actually work.
"""

import json
import re
from pathlib import Path

# Repo root is the parent of this package directory.
HERE = Path(__file__).resolve().parent.parent
CONFIG_PATH = HERE / "config.json"


def _format_entry(entry):
    """Render a board dict in config.json's hand-formatted 2-line style:
        { "name": ..., "type": ..., <overrides>,
          "url": ... }"""
    head = {k: v for k, v in entry.items() if k != "url"}
    head_str = ", ".join(f'"{k}": {json.dumps(v)}' for k, v in head.items())
    return f'    {{ {head_str},\n      "url": {json.dumps(entry["url"])} }}'


def _append_to_config(entry):
    """Insert the board into config.json in its `type` group, keeping every type in
    one contiguous block. If the type already has boards, the entry joins the end of
    that block. If it's a brand-new type, a new block is opened in canonical adapter
    order (matching jobwatch.ADAPTERS) and set off by a blank line — so new additions
    land in their proper group rather than being dumped onto whatever sits last.
    Returns (group_label, count_of_type)."""
    from jobwatch import ADAPTERS  # canonical group order — single source of truth
    order = list(ADAPTERS)
    rank = lambda t: order.index(t) if t in order else len(order)

    text = CONFIG_PATH.read_text()
    boards = json.loads(text)["boards"]
    new_type = entry["type"]
    block = _format_entry(entry)

    # Map each board to its text span. Board dicts are flat (no nested braces),
    # so `{...}` blocks in the boards-array region line up 1:1 with `boards`.
    arr_start = text.index("[", text.index('"boards"'))
    blocks = list(re.finditer(r"\{[^{}]*\}", text[arr_start:]))
    if len(blocks) != len(boards):
        # Safety fallback: structure not as expected → append at end of array.
        marker = text.rfind("\n  ]")
        prev = text.rfind("}", 0, marker)
        new = text[:prev + 1] + ",\n" + block + text[prev + 1:]
        json.loads(new)
        CONFIG_PATH.write_text(new)
        return "(end, fallback)", sum(b.get("type") == new_type for b in boards) + 1

    same = [i for i, b in enumerate(boards) if b.get("type") == new_type]
    if same:
        # Existing group: tack onto the end of the block, no separator.
        insert_at = arr_start + blocks[same[-1]].end()
        new = text[:insert_at] + ",\n" + block + text[insert_at:]
        label = new_type
    else:
        # New group: open it in canonical order, set off by a blank line. Insert
        # after the last board whose type ranks at or before the new one.
        before = [i for i, b in enumerate(boards) if rank(b.get("type")) <= rank(new_type)]
        if before:
            insert_at = arr_start + blocks[before[-1]].end()
            new = text[:insert_at] + ",\n\n" + block + text[insert_at:]
        else:  # ranks before every existing board → first group in the array
            insert_at = arr_start + 1  # just after the opening "["
            new = text[:insert_at] + "\n" + block + ",\n" + text[insert_at:]
        label = f"(new '{new_type}' group)"

    json.loads(new)  # validate before writing
    CONFIG_PATH.write_text(new)
    return label, len(same) + 1


def _verify(entry):
    """Run the actual jobwatch fetch path for this board and report what it gets.
    Returns (job_count, error) — the same (never-raises) contract jobwatch uses,
    so this is exactly how the board will behave in a real run."""
    from jobwatch import fetch_board  # imports the adapter registry
    _name, jobs, error = fetch_board(entry)
    return len(jobs), error
