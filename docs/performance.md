# Runtime performance

Notes on why a full `python3 jobwatch.py` run takes as long as it does, what has already
been done about it, and what is left. The **[Dead ends](#dead-ends--measured-do-not-retry)**
section at the bottom matters most: those are changes that look obviously correct and
would break the run, with the HTTP responses proving it.

All figures here are measurements against the live boards in `config.json` (303 boards),
not estimates, unless explicitly marked as modelled.

## Where it stands

| | Wall time |
|---|---|
| Original (8 workers, resolving every placeholder) | ~1,900 s (modelled) |
| After the multi-location prefilter + delay scaling | **445 s** (measured) |
| After raising the worker count to 32 | **186 s** (measured) |

The two measured points are full runs of all 303 boards. The original figure is modelled
from per-board probes rather than a stopwatch, so treat it as approximate — the two later
numbers are not.

### What was done

1. **Title-prefilter before enrichment** (`adapters/workday.py`). Workday returns
   `"3 Locations"` instead of city names for multi-office postings, and the adapter used to
   resolve each one via a per-job detail request. Measured across the 98 responding Workday
   boards: 43,072 postings, **16.3% carry the placeholder** (~8,310 detail requests per run),
   but only **0.12%** both carry it *and* pass the title filter (~51 jobs). The rest were
   fetched and then discarded on title alone.

   `jobwatch.fetch_board` now passes the title half of the filter down as an advisory
   `title_ok` hint. It is not part of the adapter contract — the job is returned either way,
   only the enrichment is skipped, and `matches()` still runs in full afterwards. Both paths
   call the same `_title_matches()`, which is what makes it safe: a prefilter stricter than
   the real filter could strand a matching job with an unresolved placeholder, and sharing
   one function makes that impossible. `tests/test_jobwatch.py` pins the invariant.

2. **One delay knob** (`adapters/common.polite_sleep`). Nine fixed `time.sleep()` calls
   across the adapters cost ~925 s per run. They now scale by `DELAY_SCALE`, set from the
   optional `request_delay_scale` config key (default `0.2`). `1.0` restores the original
   timing, `0` disables sleeping. See GUIDE.md §6.

3. **`MAX_WORKERS` 8 → 32** (`jobwatch.py`). 303 boards at 8 workers is ~38 sequential
   waves; 32 gives ~10. The threads are almost entirely idle on network.

### Where the remaining 186 s goes

The last boards to finish in the 186 s run are the critical path:

```
successfactors  DynaTrace      2004 jobs      workday   RTX            4330 jobs
successfactors  CapGemini      1752           workday   AtkinsRealis   2000
oracle          Oracle         2303           avature   LuluLemon      1112
workday         ABB            2000           workday   Walmart        2000
workday         Hitachi        2000           workday   Airbus Canada  2000
oracle          WSP Canada     3804           workday   Accenture      2000   <- last
```

**7 of the 12 slowest are Workday**, each one a long sequential paging loop inside a single
adapter call. More workers cannot help them — that is what stage 4 addresses.

Serial cost by platform, measured at 40 workers across the 204 non-Workday boards
(99 s wall, 1,103 s serial): successfactors 412 s/31 boards, oracle 185 s/15,
radancy 176 s/10, avature 116 s/4, smartrecruiters 53 s/6, dayforce 39 s/11,
eightfold 37 s/5, phenom 37 s/17, icims 15 s/7, greenhouse 7 s/44, ashby 3 s/23.
Slowest single non-Workday board: LuluLemon (avature) at 89 s.

---

## Stage 4 — Parallel page fan-out in `fetch_workday`

The only remaining change that restructures a loop, and the one aimed at the current
critical path.

Workday's page size is capped at 20 by the server (see [Dead ends](#dead-ends--measured-do-not-retry)),
so RTX's 4,330 jobs means **217 requests issued one after another** inside one adapter call.
Accenture, Walmart, Airbus, ABB, Hitachi and AtkinsRealis are 100 sequential pages each.

Workday reports `total` on page 0 and pages by numeric offset, so every remaining request is
computable up front:

- Keep page 0 sequential — it carries the underscore-tenant retry and captures `total`.
  Both must settle before fanning out.
- Map `range(20, total, 20)` over a **module-level bounded pool** in `adapters/workday.py`
  shared across all Workday boards, so peak threads stay ~32 outer + N inner rather than
  multiplying. Inner tasks never submit to the inner pool, so there is no deadlock risk.
- Keep the per-page `if not postings` guard so a short page contributes nothing.

**Measured:** 12 consecutive Medtronic pages, **15.2 s sequential → 2.1 s at 8 threads**,
all HTTP 200.

Consequences to handle:

- **Job order within a board becomes nondeterministic.** Nothing depends on it — output is
  sorted by `(company, title)` and `seen.json` is a sorted set.
- **It trusts `total` rather than breaking on the first empty page.** Today the loop stops at
  the first empty page, which can silently truncate; fanning out over all offsets can only
  return *more* jobs. Note that many boards report exactly `total: 2000` (AtkinsRealis, ABB,
  Hitachi, Walmart, Airbus, Accenture) — that is a server-side cap, not a bug.
- **Error semantics shift.** A failure on page 7 surfaces after pages 8-20 have already been
  fetched. Wrap the fan-out so the first exception propagates, matching today's
  fail-the-board behaviour rather than silently returning a partial list.

---

## Stage 5 — Robustness, not speed

Little wall-clock gain, but it bounds the worst case. In value order:

**5a. Timeout tuples.** All 28 call sites pass a bare `timeout=30`, which requests treats as
*both* connect and read timeout. A host that accepts the TCP connection then stalls burns 30 s
per request — and across a paging loop that is unbounded per board. Replace with a shared
`TIMEOUT = (5, 20)` in `adapters/common.py`. Mechanical, and the highest-value item here.

**5b. Per-board deadline.** `fetch_board` stamps a budget onto the board dict; the long paging
loops check it and return what they have. Today one wedged board holds a worker slot
indefinitely, and `as_completed()` has no `timeout=`, so it can block process exit. A partial
result still feeds the diff, which beats losing the board entirely. Loops that need the check:
workday, oracle, radancy, successfactors (both paths), avature, icims (both paths), phenom,
eightfold. Note the honest limit: `cancel_futures` cannot cancel an already-running future, so
5a is what actually bounds the in-flight thread.

**5c. iCIMS page size.** `_HOME_PAGE_SIZE = 10` → `100`. Verified working on KPMG, AMD and
PepsiCo. Only ~15 s across those 4 boards, so this is cheap rather than valuable.

**5d. SuccessFactors warm-up.** `_fetch_modern` does a warm-up GET that only cookie-bearing
modern sites need; on a classic-template board it is a wasted full page load. 23 of 31 boards
are classic, ~0.4 s each. Marginal.

**5e. Session reuse.** Measured **0-10%** — server latency dominates, so this is hygiene, not
speed. Workday, 8 pages: 9.24 s module-level vs 9.29 s with a shared Session (0%).
SmartRecruiters: 0.355 s/req vs 0.318 s/req (10%).

If done, use **per-thread sessions** via `threading.local()`, not one shared Session.
`requests.Session` is not documented as thread-safe; the cookie jar and the urllib3
`PoolManager` both have known races, and cookie cross-contamination between boards would be a
real correctness bug — Dayforce and SuccessFactors both rely on session cookies. Per-thread
sessions also cap session count at `max_workers`. The tradeoff: connection reuse then happens
*within* one board's paging loop rather than across boards, which is the useful half anyway.

Test cost is why this ranks last: five adapters build their own `requests.Session()`
(eightfold, icims ×2, dayforce, successfactors) and `tests/fakehttp.py` intercepts via
`FakeRequests.Session()`. Have adapters call `common.session()` by module attribute (not a
bound import) and add one `mock.patch.object` line to `AdapterTestCase.run_adapter`.

---

## Dead ends — measured, do not retry

Each of these looks like an obvious win and is not. The evidence is from live probes.

### Workday `limit` > 20 → hard HTTP 400

Server-enforced, verified on three separate tenants (Motorola Solutions, SOTI, Medtronic):

```
limit=20  -> 200  (20 rows, total 931)
limit=21  -> 400  {"errorCode":"HTTP_400", ...}
limit=25 / 30 / 40 / 50 / 100 / 200  -> 400
```

Shipping this trips `resp.raise_for_status()` and every Workday board returns zero jobs —
a total outage of 99 boards. Twenty is not a client choice.

### Workday `searchText` narrowing → silently returns everything

Filtering server-side instead of downloading whole boards seems obvious. It does not work:

```
searchText="co-op"  -> total: 931   (Motorola's ENTIRE board — the tokenizer drops the hyphen)
searchText="intern" -> total: 487   (fuzzy-matches "International")
```

A term that degrades to "everything" makes this a pessimization, not an optimization.

### Parallel query terms in phenom / eightfold / icims → not worth it

The 5 `query_terms` loops are sequential and look like an easy 5x. But the terms narrow hard,
so the theoretical worst case never materialises. Microsoft (eightfold):
`intern=36, co-op=8, coop=154, student=4, apprentice=4` — ~23 requests, 12 s. All 29
keyword-driven boards total 89 s serial.

Also note `co-op` and `coop` usually tokenize identically, so ~20% of those requests return
duplicate result sets. The adapters dedupe by id, so the waste is invisible but paid for.

### Reordering the SuccessFactors modern/classic probe → helps 23, hurts 8

`fetch_successfactors` tries modern first and falls back to classic, costing 23 of 31 boards a
wasted probe. Flipping the order just moves the cost onto the 8 genuinely modern boards.
Fix the warm-up (5d) instead.

### API quirks worth knowing

- **iCIMS careers-home** accepts `limit` but ignores `size` and `per_page`. `limit=100` verified
  on KPMG, AMD, PepsiCo.
- **Eightfold** caps at 10 results per page regardless of `num` — `num=25`, `50`, `100` all
  return 10 rows.
- **Dayforce** requires 2 setup requests (cookie prime + CSRF) before any job data, and its
  paging loop has no safety cap — termination relies entirely on `maxCount`.

### Rate limiting: none observed

Worth re-checking if a board starts failing, but as of the stage 1-3 work:

- 12 consecutive Workday pages with zero delay: all HTTP 200, no `Retry-After`.
- All 99 Workday boards hit simultaneously at 40 threads: 98/99 fine (the one failure was an
  unrelated 422).
- 204 non-Workday boards at 40 workers: zero errors.

If a vendor does start throttling, raise `request_delay_scale` in `config.json` before
changing any code.

---

## Re-measuring

```bash
cp seen.json seen.json.bak                       # a run consumes new jobs
/usr/bin/time -f "\nWALL: %e s" python3 jobwatch.py > run.out 2>&1
tail -12 run.out                                 # last to finish = critical path
grep ERROR run.out
cp seen.json.bak seen.json                       # restore so nothing is lost
```

Compare board failures against a previous run before blaming a change — `Cerebras` currently
404s because its Greenhouse slug is dead upstream, unrelated to any performance work.

To verify a change does not alter *which* jobs surface, fetch each board twice (old and new
code paths) and compare the set of jobs passing `jobwatch.matches()`, rather than comparing
run output — output depends on `seen.json` state and is not stable between runs.
