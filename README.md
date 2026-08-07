# Job Watch
Polls company **ATS career boards** directly and prints jobs that match your
filters. Remembers what it has already shown you, so each run only surfaces
what's **new** since last time. Terminal-only, no accounts, no database.

Supports 18 ATS platforms. Other types plug in as small adapters.

| ATS | URL looks like | config `type` |
|-----|----------------|---------------|
| Workday | `company.wdN.myworkdayjobs.com/.../SITE` | `workday` |
| Greenhouse | `job-boards.greenhouse.io/<token>` | `greenhouse` |
| Lever | `jobs.lever.co/<company>` | `lever` |
| Ashby | `jobs.ashbyhq.com/<board>` | `ashby` |
| Phenom | `jobs.<company>.com/.../c/...` (page has `phApp`) | `phenom` |
| SuccessFactors | `careers.<company>.com/search/?...` | `successfactors` |
| Oracle HCM | `<host>.oraclecloud.com/hcmUI/.../sites/SITE/jobs` | `oracle` |
| Radancy/TalentBrew | `<host>/search-jobs?orgIds=...` | `radancy` |
| SmartRecruiters | `careers.smartrecruiters.com/<company>/` | `smartrecruiters` |
| BambooHR | `<sub>.bamboohr.com/careers` | `bamboohr` |
| Rippling | `ats.rippling.com/<slug>/jobs` | `rippling` |
| UKG/UltiPro | `recruiting.ultipro.ca/<TENANT>/JobBoard/<guid>/` | `ukg` |
| Dayforce | `jobs.dayforcehcm.com/<locale>/<namespace>/<board>` | `dayforce` |
| iCIMS | `<sub>.icims.com/jobs/search`, or a careers-home vanity domain | `icims` |
| Eightfold | `<host>/careers?...&pid=...&sort_by=...` (page has `pcsxConfig`) | `eightfold` |
| RippleMatch | `app.ripplematch.com/v2/public/company/<slug>` | `ripplematch` |
| JazzHR | `<tenant>.applytojob.com/apply` | `jazzhr` |
| Avature | `<host>/en_US/careers/SearchJobs/?...` (page loads `avacdn.net`) | `avature` |

- **Greenhouse/Lever/Ashby**: slug is read from the last path segment; override
  with `"token"`/`"company"`/`"board"`.
- **Phenom**: reads the site's `phApp` config off the page, then queries its
  `/widgets` API with early-careers keywords (intern/co-op/student/...). Override
  the search terms per board with `"query": ["intern", ...]`.
- **Eightfold**: reads the page's CSRF token + API domain, then pages the
  `/api/pcsx/search` JSON endpoint. Like Phenom it searches early-careers keywords
  (so it doesn't pull the whole company); override per board with `"query"`. If
  the API domain can't be read from the page, pin it with `"domain": "company.com"`.

The keyword-driven adapters (Phenom, Eightfold, and iCIMS careers-home) share one
default keyword list, set once at the top level of `config.json` as `"query_terms"`.
A board's own `"query"` overrides it; leave a board's `query` unset to use the list.
- **iCIMS**: two products behind one `type`. A real `<sub>.icims.com` host is the
  classic HTML portal (fetched whole, filtered locally). A white-labeled vanity
  domain (`careers.amd.com`, `www.pepsicojobs.com`, …) runs the newer careers-home
  SPA — its `/api/jobs` JSON endpoint is keyword-searched like Phenom/Eightfold.
- **SuccessFactors**: hits the `tile-search-results` endpoint and *preserves the
  board URL's own query string* — so put the company's Canada/student facet
  params right in the URL and they're applied server-side.

- **Oracle HCM**: uses the public Candidate Experience REST API (clean JSON).
  Vanity domains (e.g. `jobs.nokia.com`) only proxy the UI, not the API — for
  those, pass the real `*.oraclecloud.com` backend via `"host": "..."`.
- **Avature**: vanity portals (`jobs.siemens.com`, `emplois.bnc.ca`, `jobs.ea.com`)
  with no public JSON API — the jobs are scraped from the listing HTML and paged by
  following the "Next" link. Put the company's early-career filter params **in the
  board URL**; they're applied server-side, and there's no keyword search to fall
  back on.

Note: Phenom, SuccessFactors, Eightfold, the iCIMS careers-home SPA, JazzHR and
Avature are HTML/keyword-scraped rather than clean JSON APIs, so they're a bit more
fragile — a site redesign can break them, and not every company on those platforms
exposes the standard endpoints.

## Setup

Requires Python 3 and `requests` (you already have both).

```bash
cp config.example.json config.json
# edit config.json — add your boards and tune filters
python3 jobwatch.py
```

## Identifying a board's ATS (`probe.py`)

Not sure what platform a careers page runs on? Point `probe.py` at it:

```bash
python3 probe.py https://www.somecompany.com/careers
python3 probe.py https://jobs.lever.co/acme --add        # append to config.json
python3 probe.py https://careers.acme.com --name "Acme"  # set the display name
python3 probe.py "Acme, https://careers.acme.com" --add  # ...or pin it inline
python3 probe.py URL1 URL2 URL3 --add                    # batch: many URLs at once
python3 probe.py --file urls.txt --add                   # batch from a file ('-' = stdin)
python3 probe.py --file companies.md --add               # ...including a Markdown file
```

Give a name inline as `Display Name, https://url` — or separate the two with a
**tab or 2+ spaces** (`Display Name<TAB>https://url`), so a pasted `Name<TAB>url`
list drops straight into a `--file`. One entry per line in a `--file`, or as a
quoted positional arg. When present it **pins** the config entry's name so probe
never has to guess it from the domain — handy for boards whose host is an opaque
tenant slug (`fil` → "Fidelity Canada", `ejia` → "S&C Electric"). A single space
between name and URL does *not* pin (so prose like "apply at https://…" isn't
misread); lines without a name still work as before (the name is guessed/looked up).

With several URLs it processes each, then prints a summary; `--add` adds every one
that verifies. `--name` only applies to a single URL. `--file` pulls every http(s)
URL out of the file, so a **Markdown** list works as-is — `- [Name](url)` links,
bullets, tables, or prose. Markdown headings (`#`) and `<!-- ... -->` comments are
ignored, and duplicate URLs are de-duped.

It reports the detected ATS (one of the supported types, a recognized-but-
unsupported one like Jobvite/Workable, or "unknown") and prints a ready-to-paste
`config.json` board entry. It works three ways, strongest first: the URL is
already an ATS domain; the page **white-labels** a Greenhouse/Lever/Ashby backend
(it extracts the real slug and confirms it via the public API); or it **guesses**
the slug from the domain (low-confidence — verify the listed jobs are the right
company). `--add` first **verifies the board actually works** — it runs the same
fetch jobwatch will and adds it as long as the fetch **succeeds** (a reachable
board with zero current postings is still valid and gets added, with a note); it
refuses if the fetch **errors** (a board that errors would just fail every run).
When it adds, it inserts the entry into `config.json` **in its ATS group** (the file
keeps each `type` in one contiguous block, in canonical adapter order), preserving
the file's style. It never adds a type with no adapter.

## Adding a board

Open the company's careers page. If the URL looks like
`https://COMPANY.wdN.myworkdayjobs.com/.../SITE`, it's Workday. Add:

```json
{ "name": "Shopify", "type": "workday",
  "url": "https://shopify.wd3.myworkdayjobs.com/en-CA/external" }
```

The tool figures out the JSON API endpoint from the URL automatically. If a
board returns nothing, pass the site slug explicitly with `"site": "..."`.

## Filters

All matching is whole-word and case-insensitive (so `intern` matches "Internship"
but not "Internal"). A keyword starting with `re:` is instead treated as a raw,
case-sensitive regex — e.g. `re:\bI{1,2}\b` in the level group counts a trailing
entry-level roman numeral ("Security Analyst **I**") as early-career, without
matching mid/senior "III" roles.

- `title_groups` — a list of keyword-lists. A title must match **at least one
  keyword in every group** (AND across groups, OR within a group). Use this to
  require two things at once, e.g. group 1 = early-career terms, group 2 = your
  tech domain. A "Client Advisor Intern" matches group 1 but not group 2, so it's
  dropped; a "Software Developer Co-op" matches both, so it's kept.
- `title_any` — simpler alternative to `title_groups`: keep if the title contains
  at least one of these. Ignored when `title_groups` is set.
- `title_none` — drop a job if its title contains any of these (e.g. `senior`).
- `location_any` — keep only if the location contains one of these.
- `location_none` — drop a job if its location contains any of these. This is how
  foreign postings get filtered out.
- `location_rescue` — the exception to `location_none`: a location matching one of
  these is kept even though `location_none` matched it. Needed because a single
  posting can name several countries at once — "Remote (US | Canada)" is dropped by
  a `location_none` of `united states` unless `canada` is in the rescue list.

### Tuning it
To **broaden** results, add words to a group (more synonyms = more matches).
To **narrow**, add a new group (every group is an extra requirement) or add
unwanted words to `title_none`. To go back to "all early-career roles," delete
the second (tech-domain) group from `title_groups`.

Leave a list empty (`[]`) to skip that check.

## Running it on a schedule (later)

It's built to run manually. To get pinged automatically, add a cron entry:

```
0 9,13,17 * * *  cd /home/sp/Documents/Projects/Job-Watch && python3 jobwatch.py >> log.txt 2>&1
```

To add desktop/email/Discord notifications, hook into the `new_jobs` list at
the end of `main()`.

## Adding other ATS platforms

Each ATS is a JSON API returning a list of jobs. Add `adapters/<name>.py` with a
`fetch_<name>(board)` that returns the normalized job dict (`id, title, location,
posted, url, company`), then import it into `jobwatch.py` and register it in the
`ADAPTERS` dict. ~30 lines each. Shared helpers (`HEADERS`, `BROWSER_UA`,
`_slug_from_url`) live in `adapters/common.py`.

Before optimizing an adapter's paging, read [docs/performance.md](docs/performance.md) —
it records where a run's time actually goes, and which obvious-looking speedups are
measured dead ends (raising Workday's page size past 20 returns HTTP 400 and would take
out all 99 Workday boards).
