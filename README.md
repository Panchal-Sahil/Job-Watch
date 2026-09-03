# Job Watch

Job aggregators rank by algorithm and lag hours behind the company's
own career page. jobwatch polls career boards, applies your filters, and
prints what's **new** since your last run. It tracks what you've seen,
so repeat runs show only fresh postings.

Terminal-only, no accounts, no database. Reads public career board APIs
for personal use.

Supports 23 ATS platforms. Other types plug in as small adapters.

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
| Jobvite | `jobs.jobvite.com/<slug>` | `jobvite` |
| Avature | `<host>/en_US/careers/SearchJobs/?...` (page loads `avacdn.net`) | `avature` |
| Gem | `jobs.gem.com/<slug>` | `gem` |
| Workable | `apply.workable.com/<slug>` | `workable` |
| Yello | `<sub>.yello.co/job_boards/<board_id>` | `yello` |
| ZohoRecruit | `<org>.zohorecruit.com/jobs/<page>` | `zohorecruit` |

- **Greenhouse/Lever/Ashby**: the slug comes from the last path segment. Override
  with `"token"`/`"company"`/`"board"`.
- **Phenom**: reads `phApp` config off the page, then queries the
  `/widgets` API with early-career keywords (intern/co-op/student/...). Override
  per board with `"query": ["intern", ...]`.
- **Eightfold**: reads the page's CSRF token and API domain, then pages
  `/api/pcsx/search`. Like Phenom, searches early-career keywords instead of
  pulling the whole company. Override per board with `"query"`. Pin the API
  domain with `"domain": "company.com"` if it can't be read from the page.

The keyword-driven adapters (Phenom, Eightfold, and iCIMS careers-home) share one
default keyword list: `"query_terms"` at the top level of `config.json`.
A board's own `"query"` takes precedence; leave it unset to use the default.
- **iCIMS**: two products behind one `type`. A `<sub>.icims.com` host is the
  classic HTML portal, fetched whole and filtered locally. A white-labeled vanity
  domain (`careers.amd.com`, `www.pepsicojobs.com`, …) runs the newer careers-home
  SPA; its `/api/jobs` endpoint takes keyword searches like Phenom/Eightfold.
- **SuccessFactors**: hits `tile-search-results` and keeps the board URL's
  query string intact. Put the company's Canada/student facet params in the URL
  and they apply server-side.

- **Oracle HCM**: uses the Candidate Experience REST API (clean JSON).
  Vanity domains (e.g. `jobs.nokia.com`) proxy only the UI, not the API. For
  those, pass the real `*.oraclecloud.com` backend via `"host": "..."`.
- **Avature**: vanity portals (`jobs.siemens.com`, `emplois.bnc.ca`, `jobs.ea.com`)
  with no public JSON API. Jobs come from the listing HTML, paged by following
  the "Next" link. Put the company's early-career filter params **in the
  board URL**; they apply server-side. No keyword search fallback.

Phenom, SuccessFactors, Eightfold, the iCIMS careers-home SPA, JazzHR, Jobvite,
Avature, Yello, and ZohoRecruit scrape HTML or search by keyword. A site
redesign can break them, and some companies don't expose the standard endpoints.

## Setup

Requires Python 3 and `requests`.

```bash
cp config.example.json config.json
# edit config.json — add your boards and tune filters
python3 jobwatch.py                          # poll all boards
python3 jobwatch.py --board Akamai           # only boards matching "Akamai" (name or URL)
python3 jobwatch.py --type oracle            # only Oracle boards
python3 jobwatch.py --board Palo --type radancy  # combine both
python3 jobwatch.py --board Akamai --raw     # skip filtering, show every job
```

## Identifying a board's ATS (`probe.py`)

`probe.py` identifies which ATS a careers page runs on:

```bash
python3 probe.py https://www.somecompany.com/careers
python3 probe.py https://jobs.lever.co/acme --add        # append to config.json
python3 probe.py https://careers.acme.com --name "Acme"  # set the display name
python3 probe.py "Acme, https://careers.acme.com" --add  # ...or pin it inline
python3 probe.py URL1 URL2 URL3 --add                    # batch: many URLs at once
python3 probe.py --file urls.txt --add                   # batch from a file ('-' = stdin)
python3 probe.py --file companies.md --add               # ...including a Markdown file
```

Give a name inline as `Display Name, https://url`, or separate the two with a
**tab or 2+ spaces** (`Display Name<TAB>https://url`), so a pasted `Name<TAB>url`
list drops into a `--file`. One entry per line in a `--file`, or as a
quoted positional arg. Pinning a name prevents probe from guessing it from the
domain, which helps for boards whose host is an opaque tenant slug (`fil` →
"Fidelity Canada", `ejia` → "S&C Electric"). A single space between name and URL
does *not* pin (so prose like "apply at https://…" isn't misread); lines without a
name still work.

With several URLs, probe processes each and prints a summary; `--add` adds every
one that verifies. `--name` applies to a single URL only. `--file` pulls every
http(s) URL out of the file, so a **Markdown** list works as-is: `- [Name](url)`
links, bullets, tables, or prose. Probe skips Markdown headings (`#`),
`<!-- ... -->` comments, and duplicate URLs.

Probe tries three strategies, strongest first: the URL is an ATS domain; the
page **white-labels** a Greenhouse/Lever/Ashby backend (probe extracts the real
slug and confirms via the public API); or probe **guesses** the slug from the
domain (low confidence, so check that the listed jobs belong to the right
company). Probe also reports recognized-but-unsupported platforms (Brassring,
Taleo, etc.) or `unknown`, and prints a ready-to-paste `config.json` entry.

`--add` first **verifies the board works**: probe runs the same fetch jobwatch
would, and adds the board as long as that fetch **succeeds** (a reachable board
with zero current postings still goes in, with a note). Probe refuses a board
that errors, since it would fail on each run. Entries land in `config.json`
**in their ATS group** (one contiguous block per type, in canonical adapter
order), matching the file's style. Probe skips any type with no adapter.

## Adding a board

Open the company's careers page. If the URL looks like
`https://COMPANY.wdN.myworkdayjobs.com/.../SITE`, it's Workday. Add:

```json
{ "name": "Shopify", "type": "workday",
  "url": "https://shopify.wd3.myworkdayjobs.com/en-CA/external" }
```

The adapter derives the API endpoint from the URL. If a board returns nothing,
pass the site slug with `"site": "..."`.

## Filters

All matching is whole-word and case-insensitive (so `intern` matches "Internship"
but not "Internal"). A keyword starting with `re:` is a raw, case-sensitive
regex: `re:\bI{1,2}\b` in the level group counts a trailing entry-level roman
numeral ("Security Analyst **I**") as early-career, without matching mid/senior
"III" roles.

- `title_groups`: a list of keyword-lists. A title must match **at least one
  keyword in every group** (AND across groups, OR within a group). Use this to
  require two things at once, e.g. group 1 = early-career terms, group 2 = your
  tech domain. A "Client Advisor Intern" matches group 1 but not group 2, so it
  drops; a "Software Developer Co-op" matches both, so it stays.
- `title_any`: simpler alternative to `title_groups`. Keeps a job if the title
  contains at least one of these. Ignored when `title_groups` is set.
- `title_none`: drops a job if its title contains any of these (e.g. `senior`).
- `location_any`: keeps a job only if the location contains one of these.
- `location_none`: drops a job if its location contains any of these. Use this
  to filter out foreign postings.
- `location_rescue`: the exception to `location_none`. A location matching one
  of these survives even when `location_none` would drop it. Needed because one
  posting can name several countries: "Remote (US | Canada)" gets dropped by
  `location_none: "united states"` unless `canada` is in the rescue list.

### Tuning it
To **broaden** results, add words to a group (more synonyms = more matches).
To **narrow**, add a new group (every group is an extra requirement) or add
unwanted words to `title_none`. To go back to "all early-career roles," delete
the second (tech-domain) group from `title_groups`.

Leave a list empty (`[]`) to skip that check.

## Running it on a schedule (later)

Add a cron entry to run on a schedule:

```
0 9,13,17 * * *  cd /home/sp/Documents/Projects/Job-Watch && python3 jobwatch.py >> log.txt 2>&1
```

Hook into the `new_jobs` list at the end of `main()` for
desktop/email/Discord notifications.

## Adding other ATS platforms

Each ATS adapter reads a JSON API or scrapes HTML and returns a list of jobs.
Add `adapters/<name>.py` with a `fetch_<name>(board)` that returns the normalized
job dict (`id, title, location, posted, url, company`), then import it into
`jobwatch.py` and register it in the `ADAPTERS` dict. Add detection rows in
`probe/signatures.py` (`HOST_RULES` / `HTML_SIGNATURES` / `SUPPORTED_TYPES`),
and optionally a confirmer in `probe/confirm.py`. Run the tests — one enforces
that `SUPPORTED_TYPES` stays in sync with `ADAPTERS`. Shared helpers (`HTTP`,
`HEADERS`, `BROWSER_UA`, `TIMEOUT`, `new_session`, `polite_sleep`,
`_slug_from_url`) live in `adapters/common.py`.

Before optimizing an adapter's paging, read
[docs/optimization-plan.md](docs/optimization-plan.md): where a run's time goes,
and which speedups are measured dead ends (raising Workday's page size past 20
returns HTTP 400 and takes out all 99 Workday boards).
