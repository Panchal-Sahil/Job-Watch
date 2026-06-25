# jobwatch — everyday guide

Everything you edit lives in **one file: `config.json`**. You never need to touch
the Python. Two sections: `filters` (what you want) and `boards` (where to look).

---

## 1. Running it

```bash
cd /home/sp/Documents/Projects/jobwatch
python3 jobwatch.py
```

- You'll see `[n/51]` lines tick by as each board is checked (~2–3 min).
- At the end it prints **only jobs that are new since your last run**.
- "No new matching jobs" = nothing new posted that matches your filters. Normal.

**See everything fresh (e.g. after changing filters):**
```bash
rm -f seen.json
python3 jobwatch.py
```
`seen.json` is the tool's memory of what it has already shown you. Deleting it
makes every current match count as "new" again.

---

## 2. Adding a website (board)

### Step A — figure out which platform the careers site uses
Look at the job board's URL:

| If the URL contains…            | type to use        |
|---------------------------------|--------------------|
| `myworkdayjobs.com` / `myworkdaysite.com` | `workday`     |
| `greenhouse.io`                 | `greenhouse`       |
| `lever.co`                      | `lever`            |
| `ashbyhq.com`                   | `ashby`            |
| a page whose source has `var phApp` | `phenom`       |
| `/search/?...` SAP-style site   | `successfactors`   |

(Not sure? Paste the link to Claude and ask which type — or just try one and run it.)

### Step B — add an entry to the `boards` list
Open `config.json`, and add a `{ ... }` block inside `"boards": [ ... ]`.
**Put a comma after the previous entry.** Examples:

```json
{ "name": "Shopify", "type": "workday",
  "url": "https://shopify.wd3.myworkdayjobs.com/en-CA/external" },

{ "name": "Figma", "type": "greenhouse",
  "url": "https://job-boards.greenhouse.io/figma" },

{ "name": "Notion", "type": "ashby",
  "url": "https://jobs.ashbyhq.com/notion" }
```

- `name` — whatever label you want to see in the output.
- `type` — from the table above.
- `url` — paste the careers-page URL.

### Step C — test just that one board
Easiest: run the whole tool and watch its `[n/51]` line. If your new board shows
`0 jobs` or `ERROR`, something's off (wrong type or URL) — see Troubleshooting.

### Notes per type
- **SuccessFactors:** keep the company's own filter params in the URL (e.g.
  `?locationsearch=Canada`) — they're applied automatically.
- **Phenom:** by default it searches early-careers keywords. Override per board
  with `"query": ["intern", "new grad"]`.
- **Greenhouse/Lever/Ashby:** the slug is the last bit of the URL; if it guesses
  wrong, add `"token"` / `"company"` / `"board"` explicitly.

---

## 3. Editing what counts as a match (`filters`)

Three knobs:

```json
"filters": {
  "title_groups": [ [ ...group 1... ], [ ...group 2... ] ],
  "title_none":   [ "senior", "manager", ... ],
  "location_any": [ "canada", "toronto", ... ]
}
```

- **`title_groups`** — list of word-lists. A job's title must match **at least one
  word in EVERY group**. Right now: group 1 = early-career, group 2 = tech domain.
  - Want *more* results from a group? Add synonyms to it.
  - Want *fewer*? Add another group (each group is an extra requirement).
  - Want *all* early-career roles again (not just tech)? Delete group 2.
- **`title_none`** — drop a job if its title has any of these words.
- **`location_any`** — keep only if the location contains one of these.

Matching is whole-word and case-insensitive: `intern` matches "Internship" but not
"Internal". A word starting with `re:` is a regex (advanced; e.g. the roman-numeral
entry-level rule).

**After editing filters, do a fresh run** (`rm -f seen.json && python3 jobwatch.py`)
so you see the effect on all current jobs.

---

## 4. Common edits, quick reference

| I want to…                          | Do this |
|-------------------------------------|---------|
| Add a company                       | new block in `boards` (section 2) |
| Stop seeing senior roles            | add the word to `title_none` |
| Only software roles, no security    | shrink group 2 to just software words |
| Include a new city                  | add it to `location_any` |
| See ALL early-career roles again    | delete group 2 from `title_groups` |
| Re-show everything from scratch     | `rm -f seen.json` then run |
| Make runs faster                    | raise `"max_workers"` in config (default 8) |

---

## 5. Troubleshooting

- **A board shows `ERROR`** — usually a wrong `type` or a changed/blocked site.
  Open its URL in a browser to confirm it still works.
- **A board shows `0 jobs`** — that company may have nothing posted, or the slug is
  wrong. Try adding the explicit `site`/`token`/`board` field.
- **`json.decoder.JSONDecodeError` on startup** — you broke `config.json` (usually a
  missing or extra comma). Every entry needs a comma after it except the last one.
- **Looks frozen** — it isn't; watch the `[n/51]` lines. A 2–3 min run is normal.
