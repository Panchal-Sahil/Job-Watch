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
| After the parallel Workday page fan-out | **147 s** (measured) |

The measured points are full runs of all 303 boards. The original figure is modelled
from per-board probes rather than a stopwatch, so treat it as approximate — the later
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

4. **Parallel page fan-out in `fetch_workday`** (`adapters/workday.py`). Page 0 stays
   sequential — it settles the underscore-tenant retry, which rewrites the endpoint, and
   carries `total`. Every remaining offset is then known, so `range(20, total, 20)` goes
   out in parallel instead of one round-trip after another. Per board: Medtronic 63.1 s →
   10.2 s, Magna 64.6 s → 10.8 s, both returning **identical job-id sets**.

   Two things the plan for this did not anticipate, both found by running it:

   **Workday throttles per pod, so the fan-out is per pod.** A single shared 32-thread
   pool drew 429s from four separate wd5 tenants in one run (General Motors, RTX, Aptiv,
   Thomson Reuters). That is not chance: wd5 is 19 of 99 Workday boards but the heaviest
   pod by request volume — 770 requests a run, with RTX (217 pages), GE Vernova (104) and
   NVIDIA (100) alone accounting for 421 — and a 32-wide burst at one pod is nothing
   sequential paging ever produced. Pages now fan out over **one bounded pool per pod**
   (`_pool_for`, keyed on the `wd5.myworkdayjobs.com` half of the hostname), 8 deep. Not
   per board, which lets several tenants burst at one pod at once; not one global pool
   with a per-pod semaphore, which lets a busy pod's backlog park every thread and starve
   the other pods. Eight is below the ~19 concurrent requests a pod already absorbed under
   sequential paging without complaint.

   **A 429 used to cost the whole board.** `_post_page` retries up to 4 times, honouring
   `Retry-After` when it parses as seconds and backing off 2/4/8 s otherwise, on page 0 as
   well as fan-out pages. This is worth having independently of pacing: during this work
   the *old* sequential code took a 429 on GE Vernova and lost all 2,072 jobs, where the
   new code retried through it. The backoff is deliberately not scaled by `DELAY_SCALE` —
   it is the server saying wait, not our own politeness margin.

### Where the remaining 147 s goes

The last boards to finish in the 147 s run are the critical path:

```
workday         Airbus Canada  2000 jobs      workday   RTX            4328 jobs
oracle          Oracle         2303           phenom    Aptiv           717
successfactors  DynaTrace      2004           phenom    Thomson Reuters 387
successfactors  CapGemini      1752           avature   LuluLemon      1112   <- last
oracle          WSP Canada     3803
```

Workday is **no longer the critical path** — the tail is now oracle, successfactors and a
single avature board. The remaining Workday entries finish alongside them rather than
after them. Nothing in stage 5 changes this; the next real win would be applying the same
offset fan-out to oracle and successfactors, neither of which has been checked for whether
it reports a usable total up front.

Serial cost by platform, measured at 40 workers across the 204 non-Workday boards
(99 s wall, 1,103 s serial): successfactors 412 s/31 boards, oracle 185 s/15,
radancy 176 s/10, avature 116 s/4, smartrecruiters 53 s/6, dayforce 39 s/11,
eightfold 37 s/5, phenom 37 s/17, icims 15 s/7, greenhouse 7 s/44, ashby 3 s/23.
Slowest single non-Workday board: LuluLemon (avature) at 89 s.

---

## Notes on the shipped fan-out

Two design points that are easy to undo by accident:

- **Job order stays deterministic.** The fan-out uses `Executor.map`, which yields results
  in submission order, so pages land in offset order and the returned list is identical to
  the sequential version's. Nothing downstream depends on it — output is sorted by
  `(company, title)` and `seen.json` is a sorted set — but the equivalence check above
  compares ordered output, and switching to `as_completed` would break that, not the run.
- **It trusts `total` rather than breaking on the first empty page.** The old loop stopped
  at the first empty page, which can silently truncate; fanning out over all offsets can
  only return *more* jobs. Many boards report exactly `total: 2000` (AtkinsRealis, ABB,
  Hitachi, Walmart, Airbus, Accenture) — that is a server-side cap, not a bug. An empty
  page 0 still short-circuits: no `total`, no fan-out.
- **A failing page still fails the whole board.** `map` re-raises the first exception when
  results are consumed, matching the old fail-the-board behaviour rather than silently
  returning a partial list — just later, after the other pages have already been fetched.

---

## Stage 5 — Robustness, not speed

Little wall-clock gain, but it bounds the worst case. In value order:

**5a. Timeout tuples — done.** Every adapter call site passed a bare `timeout=30`, which
requests treats as *both* connect and read timeout. A host that accepted the TCP connection
then stalled burned 30 s per request — and across a paging loop that is unbounded per board.
Now a shared `TIMEOUT = (5, 20)` in `adapters/common.py`.

Done alongside it, because the same run motivated both: adapters reach the network through
`common.HTTP` (a shared session, cookies refused so it stays safe across boards and threads)
or `common.new_session()` where a board needs its own cookie jar, both mounting a urllib3
`Retry` that retries **transport failures only** — `status=0`, no forcelist. That scope is
deliberate. Retrying statuses would sit underneath `workday._post_page`'s 429 backoff and
double up on a pod already asking for less, and would re-ask a board that 404s because the
company closed it.

What prompted it: one run lost Accenture, CPKC and CapGemini — the three heaviest boards —
to `HTTPSConnectionPool` timeouts, while each of them fetched fine on its own (2000 jobs /
25.4 s, 175 / 5.6 s, 1761 / 62.6 s). Nothing retried that class of error, so a single blip
cost a whole board, and `successfactors._fetch_modern`'s paging POST discarded every page
already collected on the way out. Diagnosing it was harder than it should have been: the
progress line truncated the error at 50 characters, which is *before* the part that says
whether it was a timeout or a 404, and the untruncated text went to stderr — which a
redirected run (`> jobwatch.out`) throws away. Both fixed.

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

### Rate limiting: Workday throttles per pod, everything else has stayed quiet

This section previously read "none observed", on the strength of the probes below:

- 12 consecutive Workday pages with zero delay: all HTTP 200, no `Retry-After`.
- All 99 Workday boards hit simultaneously at 40 threads: 98/99 fine (the one failure was an
  unrelated 422).
- 204 non-Workday boards at 40 workers: zero errors.

Every one of those keeps at most **one request per board** in flight. That is the load
shape sequential paging produces, and it is not the load shape a page fan-out produces —
the first version of stage 4 pushed ~32 concurrent requests at a single pod and drew 429s
from four wd5 tenants at once. Read those probes as "board-level concurrency is fine",
not "Workday has no limit".

What is known now:

- The limit is **per pod, not per tenant** — four different wd5 tenants throttled together,
  while wd3 (44 boards, comparable total request volume, spread thinly) never has.
- 8 concurrent requests per pod is fine: a full run at that pacing had zero 429s.
- Throttled state **persists past the burst that caused it**, and applies to any client on
  the IP. Plain sequential fetches of wd5 boards kept taking 429s for a while afterwards.
- Nothing outside Workday has produced a 429 at any pacing tried.

If a vendor does start throttling, raise `request_delay_scale` in `config.json` before
changing any code. For Workday specifically, `_PAGE_WORKERS` in `adapters/workday.py` is
the pod-level knob, and lowering it is the targeted fix.

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
