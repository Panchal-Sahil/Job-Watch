# jobwatch — User Guide

This is the practical, start-here guide. If you've forgotten how any of this works,
read the section you need and run the commands as shown. Everything runs in a
terminal from the project folder:

```bash
cd /home/sp/Documents/Projects/jobwatch
```

---

## 1. What it does (the 30-second mental model)

You keep a list of company career pages in **`config.json`**. You run **`jobwatch.py`**.
It visits every board, keeps only the jobs that match your **filters** (early-career +
tech, in Canada, by default), and prints the ones it **hasn't shown you before**.

- It remembers what you've seen in **`seen.json`**, so the second run only shows *new*
  postings — not the same list again.
- **`probe.py`** is a helper: point it at any careers URL and it tells you what kind of
  system that company uses, and (optionally) adds it to your `config.json` for you.

No accounts, no database, nothing runs in the background. You run it when you want to check.

---

## 2. One-time setup

You need Python 3 and the `requests` library. You almost certainly already have both.
Check:

```bash
python3 --version          # any Python 3.x
python3 -c "import requests; print('requests ok')"
```

If `requests` is missing: `pip install --user requests`.

That's it. There's nothing to build or compile.

---

## 3. Everyday use: check for new jobs

```bash
python3 jobwatch.py
```

What you'll see:

```
Fetching 155 boards (up to 8 at a time)...
  [ 1/155] SOTI                             12 jobs
  [ 2/155] Zebra Technologies               0 jobs
  ...
  [12/155] Some Company                     ERROR: HTTP 403

  3 new matching job(s):

  • Software Developer Intern
      Shopify — Ottawa, ON  (Posted 2 days ago)
      https://shopify.wd3.myworkdayjobs.com/...

  No new matching jobs.        <- (if nothing new this run)
```

How to read it:
- The numbered lines are progress — how many jobs each board returned (before filtering).
- An `ERROR:` line means that one board failed (site down, blocked, changed). **One bad
  board never stops the run** — the others still work.
- The block at the bottom is the payoff: **new jobs that matched your filters**, since the
  last time you ran it.

**Run it as often as you like.** Each run only surfaces what's new.

### About `seen.json`
This file is jobwatch's memory of every job it has already shown you. You normally never
touch it. Two useful tricks:

- **"Show me everything again from scratch":** delete it — `rm seen.json`. The next run
  treats every current posting as new (expect a big list once).
- It's safe to delete anytime; it's just a cache and gets rebuilt.

> Note: a job is only marked "seen" if it actually **matched** your filters and was shown.
> So if you loosen your filters later, previously-hidden jobs can still show up.

---

## 4. Finding out what system a company uses: `probe.py`

When you find a company's careers page and want to add it, you first need to know what
kind of board it is. `probe.py` figures that out.

```bash
python3 probe.py https://www.somecompany.com/careers
```

It prints the detected system, how confident it is, and a ready-to-paste `config.json`
entry. Example:

```
  URL:      https://job-boards.greenhouse.io/anthropic  (HTTP 200, 69473 bytes)
  ATS:      greenhouse  [supported]   (confidence: high)
  Evidence: host matches greenhouse — confirmed via API (398 jobs)
  Slug:     anthropic

  config.json board entry:

    { "name": "Anthropic", "type": "greenhouse",
      "url": "https://job-boards.greenhouse.io/anthropic" }
```

Confidence levels:
- **high** — the URL is literally on a known job-board domain. Trust it.
- **medium** — the company's own site embeds a known board behind the scenes. Usually right.
- **low** — it *guessed* the company from the domain name. **Verify the listed jobs really
  are that company's** before trusting it.

Other things it might say:
- `[NOT SUPPORTED — no adapter]` — recognized the system (e.g. Workable, Jobvite) but
  jobwatch can't read it yet. See §8 to add support.
- `unknown` — couldn't identify it. Try pasting the *actual* job-listing URL (sometimes the
  jobs live in an embedded frame with its own URL).

### Adding it automatically with `--add`

```bash
python3 probe.py https://job-boards.greenhouse.io/anthropic --add
```

`--add` does a safety check first: it fetches the board **exactly the way jobwatch will**,
and only writes it into `config.json` if that fetch **succeeds**. A reachable board with
zero open jobs right now still gets added (with a note); a board that errors is refused.
It also drops the new entry into the right place in the file (grouped with other boards of
the same type).

### Giving it the right company name

probe guesses a display name from the URL, which can come out ugly (`Jobs Ca`). Pin a
proper name three ways:

```bash
python3 probe.py https://careers.acme.com --name "Acme Corp"      # flag (single URL)
python3 probe.py "Acme Corp, https://careers.acme.com" --add      # inline, comma
python3 probe.py "Acme Corp	https://careers.acme.com" --add       # inline, TAB
```

(A *single* space between name and URL does **not** pin a name — that's so prose like
"apply at https://…" isn't misread.)

### Probing many at once

```bash
python3 probe.py URL1 URL2 URL3 --add          # several URLs in one go
python3 probe.py --file companies.md --add     # read URLs from a file
python3 probe.py --file - --add                # ...or from stdin (paste, then Ctrl-D)
```

`--file` is forgiving: it pulls every `http(s)` URL out of the file, so a **Markdown** list
(`- [Acme](https://...)`), a plain list, a table, or even prose all work. Headings (`#`)
and `<!-- comments -->` are ignored, and duplicate URLs are removed. Each line can also use
the `Name, url` / `Name<TAB>url` form to pin names. With several URLs it prints a summary at
the end and (`--add`) adds every one that verifies.

---

## 5. Adding a board by hand

You don't *need* probe — if you already know the type, just edit `config.json`. Boards live
in the `"boards"` array. Minimal entry:

```json
{ "name": "Shopify", "type": "workday",
  "url": "https://shopify.wd3.myworkdayjobs.com/en-CA/external" }
```

The supported `type` values (and what their URLs look like) are listed in **`README.md`** —
15 systems including `workday`, `greenhouse`, `lever`, `ashby`, `icims`, `oracle`, etc.

Most boards need only `name`, `type`, `url`. A few need an override when the tool can't
figure something out from the URL alone — common ones:

| Field | When you need it |
|-------|------------------|
| `"site": "..."` | Workday board returns nothing — pin the site slug |
| `"host": "..."` | Oracle vanity domain — point at the real `*.oraclecloud.com` backend |
| `"token"` / `"company"` / `"board"` | Greenhouse / Lever / Ashby behind a custom domain |
| `"query": ["intern", ...]` | Override the search keywords for one keyword-driven board |

When in doubt, let `probe.py --add` build the entry — it fills in the right overrides.

---

## 6. Tuning what counts as a "match" (filters)

All filtering lives under `"filters"` in `config.json`. Matching is **whole-word and
case-insensitive** — `intern` matches "Internship" but **not** "Internal". (A keyword
starting with `re:` is treated as a raw regex, for power-user cases.)

The knobs:

- **`title_groups`** — a list of keyword-lists. A job's title must match **at least one
  word in *every* group**. The default has two groups: group 1 = early-career words
  (intern, co-op, new grad, junior…), group 2 = tech words (software, security, data…).
  So a job must be *both* early-career *and* technical to show up.
  - A "Marketing Intern" matches group 1 but not group 2 → dropped.
  - A "Software Developer Co-op" matches both → kept.
- **`title_any`** — simpler alternative: keep if the title has *any* of these words.
  (Ignored if `title_groups` is set.)
- **`title_none`** — drop the job if its title has any of these (e.g. `senior`, `manager`,
  `lead`). A safety net against senior roles slipping through.
- **`location_any`** — keep only if the location mentions one of these (the default lists
  Canada and Canadian cities/provinces).
- **`location_none`** — drop foreign postings (`united states`, `india`, …). Smart
  exception: a posting that *also* names Canada (e.g. "Remote (US | Canada)") is kept.

### How to adjust it

- **Too few results?** Add more synonyms to a group, or remove a group entirely. Deleting
  group 2 from `title_groups` gives you *all* early-career roles, not just tech ones.
- **Too many / wrong results?** Add an extra group (every group is one more requirement),
  or add unwanted words to `title_none`.
- **Want to switch field** (say, from tech to finance)? Replace the words in group 2.
- **Turn off a check entirely?** Set its list to empty: `"location_any": []`.

### `query_terms` (top of the file)
Some big-company boards (Phenom, Eightfold, certain iCIMS) are too large to download whole,
so jobwatch *searches* them server-side using these keywords:

```json
"query_terms": ["intern", "co-op", "coop", "student", "graduate", "apprentice"]
```

This narrows those boards to early-career roles *before* your `filters` run. If you change
career focus, update this too (otherwise those boards only ever return interns/grads). A
single board can override it with its own `"query": [...]`.

---

## 7. Testing (does the code still work?)

There's an automated test suite for `probe.py`. It runs offline (no network) in under a
second. Run it after any change to the probe code:

```bash
python3 -m unittest discover -s tests
```

You want to see `OK` at the bottom. Run a single test file while iterating:

```bash
python3 -m unittest tests.test_detect          # just the detection tests
python3 -m unittest tests.test_detect -v       # verbose: list each test
```

The tests use Python's built-in `unittest` and fake the network (`tests/fakehttp.py`), so
they're deterministic — **keep it that way** (no `pip install pytest`); the whole project
is intentionally zero-extra-dependencies.

> `jobwatch.py` itself has no automated tests yet. To "test" a jobwatch change, just run it
> against your real boards and eyeball the output.

---

## 8. Adding support for a brand-new system (writing code)

Each supported system is a small **adapter** in `adapters/<name>.py`. You only need this if
probe reports a system as `[NOT SUPPORTED — no adapter]` and you want it. The shape:

1. **Write `adapters/<name>.py`** with one function `fetch_<name>(board)` that fetches that
   system's jobs and returns a list of dicts, each with exactly these six keys:

   ```python
   { "id", "title", "location", "posted", "url", "company" }
   ```

   `id` must be stable between runs (it's the dedup key — usually something like
   `"tenant:jobslug"`). Copy an existing small adapter as a template — `adapters/lever.py`
   or `adapters/greenhouse.py` are ~30 lines and the clearest. Shared helpers
   (`HEADERS`, `BROWSER_UA`, `_slug_from_url`) live in `adapters/common.py`.

2. **Register it in `jobwatch.py`**: add an import at the top and a line in the `ADAPTERS`
   dict (`"<name>": fetch_<name>`).

3. **Teach probe to recognize it** in `probe/signatures.py`: add a row to `HOST_RULES`
   (if it has its own domain) and/or `HTML_SIGNATURES` (if it's embedded in company pages),
   and add the name to `SUPPORTED_TYPES`. For Greenhouse/Lever/Ashby-style boards that need
   an API double-check, add a confirmer in `probe/confirm.py`.

4. **Run the tests** (`python3 -m unittest discover -s tests`). One test asserts that
   `SUPPORTED_TYPES` and the adapter registry stay in sync, so it'll catch a half-finished
   addition.

---

## 9. Where everything lives

```
jobwatch.py         The main program — run this to check for jobs.
probe.py            Thin launcher for the probe tool (python3 probe.py ...).
config.json         YOUR data: the list of boards + your filters. Edit this.
seen.json           jobwatch's memory of jobs already shown (auto-managed, deletable).

adapters/           One file per supported system; each reads that system's jobs.
  common.py         Shared request headers / helpers.
  workday.py, greenhouse.py, lever.py, ...

probe/              The probe tool, split into focused pieces:
  signatures.py     The detection rules (which URL/page = which system).
  confirm.py        Double-checks Greenhouse/Lever/Ashby via their public APIs.
  detect.py         The main detection logic.
  result.py         The structured result detection returns.
  config_io.py      Reads/writes config.json (the --add insertion).
  input_parse.py    Pulls names + URLs out of args and files.
  cli.py            Command-line handling.

tests/              Automated tests for probe (run with unittest).
README.md           Reference: the full table of supported systems + per-system notes.
CLAUDE.md           Notes for the AI assistant working on this repo.
GUIDE.md            This file.
```

---

## 10. Running it automatically (optional)

It's built to run by hand, but you can have cron run it on a schedule and log the output:

```cron
0 9,13,17 * * *  cd /home/sp/Documents/Projects/jobwatch && python3 jobwatch.py >> log.txt 2>&1
```

(Open your crontab with `crontab -e`.) That checks at 9am, 1pm, and 5pm daily and appends
results to `log.txt`. To get an actual notification (desktop/email/Discord), you'd hook into
the `new_jobs` list near the end of `main()` in `jobwatch.py`.

---

## 11. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `No config found...` | You're not in the project folder, or `config.json` is missing. `cd` into the folder. |
| A board shows `ERROR: HTTP 403` | That site is blocking automated requests, or changed its layout. The other boards are unaffected; you can leave it or remove the entry. |
| A board shows `0 jobs` every time | It may genuinely have nothing open, or its URL/overrides are off. Re-run `probe.py <url>` to re-check the entry. |
| Lots of irrelevant jobs | Tighten `filters` — add a group to `title_groups` or words to `title_none` (§6). |
| Nothing matches, ever | Filters too strict, or `query_terms` too narrow for the big boards. Loosen them (§6). |
| Same jobs show every run | `seen.json` may have been deleted or can't be written. Check the folder is writable. |
| probe says `unknown` | Try the real listings URL (sometimes inside an embedded frame), not the marketing page. |
| Want a clean slate | `rm seen.json` and run again — everything current is treated as new. |
