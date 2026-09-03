"""Reading from and writing to config.json."""

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
CONFIG_PATH = HERE / "config.json"


def _format_entry(entry):
    """Render a board dict in config.json's 2-line style."""
    head = {k: v for k, v in entry.items() if k != "url"}
    head_str = ", ".join(f'"{k}": {json.dumps(v)}' for k, v in head.items())
    return f'    {{ {head_str},\n      "url": {json.dumps(entry["url"])} }}'


def _append_to_config(entry):
    """Insert into the board's type group in canonical adapter order.
    Returns (group_label, count_of_type)."""
    from jobwatch import ADAPTERS
    order = list(ADAPTERS)
    rank = lambda t: order.index(t) if t in order else len(order)

    text = CONFIG_PATH.read_text()
    boards = json.loads(text)["boards"]
    new_type = entry["type"]
    block = _format_entry(entry)

    # Board dicts are flat (no nested braces) so {…} spans line up 1:1.
    arr_start = text.index("[", text.index('"boards"'))
    blocks = list(re.finditer(r"\{[^{}]*\}", text[arr_start:]))
    if len(blocks) != len(boards):
        marker = text.rfind("\n  ]")
        prev = text.rfind("}", 0, marker)
        new = text[:prev + 1] + ",\n" + block + text[prev + 1:]
        json.loads(new)
        CONFIG_PATH.write_text(new)
        return "(end, fallback)", sum(b.get("type") == new_type for b in boards) + 1

    same = [i for i, b in enumerate(boards) if b.get("type") == new_type]
    if same:
        insert_at = arr_start + blocks[same[-1]].end()
        new = text[:insert_at] + ",\n" + block + text[insert_at:]
        label = new_type
    else:
        before = [i for i, b in enumerate(boards) if rank(b.get("type")) <= rank(new_type)]
        if before:
            insert_at = arr_start + blocks[before[-1]].end()
            new = text[:insert_at] + ",\n\n" + block + text[insert_at:]
        else:
            insert_at = arr_start + 1
            new = text[:insert_at] + "\n" + block + ",\n" + text[insert_at:]
        label = f"(new '{new_type}' group)"

    json.loads(new)
    CONFIG_PATH.write_text(new)
    return label, len(same) + 1


def _verify(entry):
    """Run the real jobwatch fetch path. Returns (job_count, error)."""
    from jobwatch import fetch_board
    _name, jobs, error = fetch_board(entry)
    return len(jobs), error
