# Performance Optimization Plan for jobwatch

## Context

jobwatch polls ~696 career boards concurrently via a `ThreadPoolExecutor(max_workers=32)`.
It has already been optimized from ~1900s to ~147s through four shipped changes (title
prefilter, delay scaling, 32 workers, Workday page fan-out). The remaining 147s is
dominated by a handful of slow paged boards — LuluLemon (Avature, 89s), heavy Oracle boards
(~31s each), SuccessFactors (~60-90s each). Boards are submitted in config.json order
(type-grouped), meaning the slowest boards (Avature at positions 691-695, Oracle at
546-578) start late and extend the tail.

The existing `docs/performance.md` documents measured dead ends — do not revisit those.

---

## Optimizations (ranked by impact / effort)

### 1. Smart board scheduling — submit slowest boards first

**Why:** With FIFO submission, LuluLemon (position 692 of 696) can't start until ~660 boards
have been submitted. It starts at roughly t=40-80s and finishes at t=129-169s. If submitted
first, it starts at t=0 and finishes at t=89s.

**What:** Sort `boards` by expected fetch time (descending) before submitting to the pool.
A static type-weight map is sufficient — Avature/SuccessFactors/Oracle/Radancy first,
Workday next, single-request adapters last.

**Where:** `jobwatch.py`, ~10 lines before line 308 (the `pool.submit` loop).

**Expected:** ~40-58s saved. Critical path drops from ~147s to ~89-107s.

---

### 2. Oracle parallel page fan-out

**Why:** Oracle pages at 200/page, and the first page returns `TotalJobsCount` (confirmed at
`oracle.py:94`). WSP Canada has 3803 jobs = 20 sequential pages × ~1.6s = ~31s. With fan-out
over 8 threads: ~4-5s. 33 Oracle boards total.

**What:** After page 0 (kept sequential for vanity-host resolution), fan remaining offsets
out via a shared `ThreadPoolExecutor`, mirroring the Workday pattern. No rate limiting has
been observed on Oracle. Each Oracle board hits a distinct `*.oraclecloud.com` host, so no
per-pod pooling is needed.

**Where:** `adapters/oracle.py`, ~40-50 lines. New test in `tests/test_adapters.py`.

**Expected:** ~10-20s saved. Heavy Oracle boards drop from ~31s to ~5s each.

---

### 3. Early page termination on all-seen IDs (repeat runs only)

**Why:** On a typical repeat run, most boards have zero new jobs. Yet every paged board
fetches all pages. LuluLemon fetches 56 pages (89s) to discover zero new jobs.

**What:** Thread the `seen` set (read-only during the fetch phase) into `fetch_board` via
the board dict. In each paging adapter, after building a page's job list, check: if every
ID on the page is already seen, stop paging. Apply only to adapters with date-descending
sort order (Oracle uses `sortBy=POSTING_DATES_DESC`; Workday sorts by date). For
HTML-scraped adapters (Avature, Radancy) where sort order isn't guaranteed, use a
conservative "stop after 2 consecutive all-seen pages" rule.

**Where:** `jobwatch.py` (~5 lines to thread `seen`), ~3 lines per paging adapter
(oracle, successfactors, workday, avature, radancy, phenom, eightfold, icims,
smartrecruiters, ukg, ripplematch, dayforce).

**Expected:** On repeat runs: 50-80s saved. LuluLemon drops from 89s to ~1.6s. Critical
path shifts to whichever board has actual new jobs. On first run: 0s (no seen IDs).

**Risk:** Medium. Must verify sort-order assumptions per adapter. Incorrect early
termination would miss new jobs on later pages. The conservative 2-page rule for
unknown-sort adapters mitigates this.

---

### 4. Per-board deadline (documented as 5b, not yet shipped)

**Why:** A wedged board (server stall, infinite redirect) holds a worker slot indefinitely.
With 32 workers, losing one is 3% capacity. Not a speed gain on normal runs, but prevents
unbounded tail on degraded runs.

**What:** `fetch_board()` stamps `board["_deadline"] = time.monotonic() + 120`. Each paging
loop checks the deadline once per iteration and returns partial results if exceeded.

**Where:** `jobwatch.py` (~3 lines), ~2 lines per paging adapter (12 adapters).

**Expected:** Robustness improvement. 120s budget gives 35% headroom above the 89s
worst case.

---

### 5. Trivial wins (bundle together)

- **iCIMS page size 10→100** (`adapters/icims.py`): Already verified working. ~15s saved.
- **Connection pool tuning** (`adapters/common.py`): Add `pool_connections=64,
  pool_maxsize=64` to HTTPAdapter. Currently at default 10, which can serialize threads
  hitting the same host. 0-5s expected.
- **Increase max_workers to 48** (`jobwatch.py`): Reduces queueing for late-starting
  boards. 0-10s expected. No rate limiting observed outside Workday (which has its own
  per-pod semaphore).

---

### Not recommended (already measured as dead ends or poor ROI)

- Workday `limit` > 20 → HTTP 400
- Workday `searchText` narrowing → tokenizer defeats it
- Parallel query terms for phenom/eightfold/icims → 89s total, not worth the complexity
- asyncio migration → massive rewrite, violates pure-stdlib+requests constraint
- DNS caching → OS-level caching already handles it (~1s across 696 hosts)
- SuccessFactors warm-up skip (5d) → ~9s total, marginal
- SuccessFactors fan-out → high complexity (session thread-safety), defer until easier wins are measured

---

## Implementation order

| Step | Change | Impact | Effort |
|------|--------|--------|--------|
| 1 | Smart scheduling | 40-58s | Trivial |
| 2 | Trivial wins (iCIMS, pool tuning, workers) | 15-30s | Trivial |
| 3 | Oracle fan-out | 10-20s | Moderate |
| 4 | Per-board deadline | Robustness | Low-moderate |
| 5 | Early seen termination | 50-80s on repeats | Moderate |

Steps 1-2 could ship together as one commit. Steps 3-5 are independent and can be done in
any order.

## Verification

- Back up `seen.json`, run with `/usr/bin/time -f "\nWALL: %e s"` before and after each
  change (procedure documented in `docs/performance.md` "Re-measuring" section).
- Run `python3 -m unittest discover -s tests` after each change.
- Compare the last ~12 lines of output to see which boards finish last (the critical path).

## Critical files

- `jobwatch.py` — main loop, board submission, `fetch_board` wrapper
- `adapters/oracle.py` — fan-out target
- `adapters/common.py` — pool tuning
- `adapters/icims.py` — page size constant
- `tests/test_adapters.py` — new Oracle fan-out test
- `docs/performance.md` — update with new measurements
