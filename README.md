# Scholarship Finder — Backend

Python + SQLite backend to help Pakistani students discover scholarships and evaluate
*indicative* eligibility. Offline, deterministic, no external APIs. Eligibility results are
guidance only; always confirm with the scholarship's official source.

## Requirements
- Python 3.9+ (developed and checked on 3.12)
- `pip install -r requirements.txt` (pandas, openpyxl, pytest)

## Database
```bash
python -c "from src.database import init_db; init_db(); print('Database ready')"
```
- Default file: `data/scholarships.db` (project root); the `data/` folder is created on demand.
- `init_db()` is idempotent. Schema creation, column migration and index creation run in **one
  transaction**; if any step fails everything is rolled back and a `RuntimeError` explains why.
  Existing tables are never dropped or recreated — missing columns (e.g. `external_id`) are added
  and existing rows are kept.
- `src.database.get_db_path()` resolves the path. Overriding either `DB_PATH` or the legacy
  `DATABASE_PATH` is honoured; overriding them to two different places raises an error instead of
  guessing.

## Importing a workbook
```python
from src.data_import import import_workbook

result = import_workbook("path/to/workbook.xlsx")
# optional: resolve an ambiguous header yourself
result = import_workbook("path/to/workbook.xlsx", column_overrides={"Country": "study_destination"})
```
`result` keys: `records_inserted`, `records_skipped`, `records_duplicate`, `records_incomplete_key`,
`sheets_processed`, `errors`, `warnings`. **Check `errors` and `warnings` after every import.**

- The workbook is opened read-only and never modified. Every source row is kept in `raw_data` (JSON).
- **Header row detection:** the header may be below title rows (first 15 rows scanned; the first row
  with ≥2 recognised headers wins). A warning reports where it was found.
- **Ambiguous headers are not mapped.** `Country, State, Marks, Percentage, CGPA, GPA, Income, URL,
  Link, Website, City, Province, District, Region, Source, Reference, Origin` (and the 60-field
  headers `minimum_grade`, `income_threshold`, `province_region`) stay unmapped: values are preserved
  in `raw_data` and a warning lists what they could mean. Explicit headers such as
  `Study Destination`, `Minimum CGPA`, `Minimum Marks`, `Application URL`, `Source URL`,
  `Eligible Provinces`, `Household Income Limit` map normally. Use `column_overrides` to decide an
  ambiguous header yourself (target must be a canonical field; otherwise an error is returned and
  nothing is imported).
- Generic-but-reasonable headers (`Level`, `Amount`, `Destination`, …) still map with a warning.
- Metadata sheets (contents/index/summary, `START_HERE`, dictionary, exclusions, verify queue,
  region index) are skipped.
- **Deduplication** (missing data never merges records):
  1. same `external_id` (workbook ID such as `opportunity_id`) ⇒ duplicate; different IDs ⇒ always distinct;
  2. complete key `name + provider + deadline` (deadline must parse; formats are normalised) ⇒ duplicate;
     a different deadline is a different cycle;
  3. key incomplete (no provider, or missing/unparseable deadline) ⇒ duplicate **only** if every
     imported field and the raw source row are identical (an exact re-import); otherwise inserted as a
     distinct record and counted in `records_incomplete_key` (with a warning).
  If the duplicate check itself fails, the row is **not inserted** and an error is reported.
- **Transactions:** each sheet is imported in one transaction. An unexpected failure rolls that sheet
  back completely (nothing from it is kept or counted) and adds an error; other sheets are unaffected.
  A single row whose INSERT fails is reported in `errors` and not counted.

## Eligibility
```python
from src.eligibility import check_eligibility
r = check_eligibility(profile, scholarship)            # optional: today=date(...) for tests
# {"eligibility_status": "eligible|potentially_eligible|insufficient_information|ineligible",
#  "reasons": [...], "missing_information": [...]}
```
Principle: a criterion **fails** only when both sides are clearly understood. Anything ambiguous,
unrecognised or malformed is *unknown* and goes to `missing_information`; it never counts as a pass.

- `eligible` needs at least one verified, student-specific criterion and nothing unknown. A scholarship
  with **no recorded criteria** is `insufficient_information`, never `eligible`. An open status or
  future deadline alone does not make a student `potentially_eligible`.
- **Marks vs CGPA:** `marks`/`percentage` are compared only with `minimum_marks`; `cgpa`/`gpa` only with
  `minimum_cgpa`. No conversion. Percentage accepts `75`, `75%`, `850/1100`; a value that looks like a
  GPA (≤4.5, or `x/4`) in a marks field is unknown. CGPA is accepted on a 4.0 scale only; `85`, `3.5/5`
  or `4.5` are unknown. Raw obtained marks without a total (e.g. `850`) are unknown.
- **Nationality:** `Pakistani`, `Pakistan`, `PK`, `Pakistani national/citizen` are equivalent. Qualified
  forms (`Overseas Pakistani`, `Dual …`) are unknown. A mismatch is declared only when Pakistan is on
  one side and the other side is a plain, different value. `All`/`Any nationality` = unrestricted.
- **Education / qualification:** conservative aliases only (`BS, BSc, BA, BBA, Bachelors, Undergraduate`
  → bachelor; `MS, MSc, MA, Masters` → master; `PhD, Doctorate`; `FSc, FA, ICS, HSSC, A Level`
  → intermediate; `Matric, SSC, O Level`). **Not aliased:** `Postgraduate`, `Graduate`, `PG`, `MPhil`
  (as a level), associate/diploma — these stay unknown. Parenthetical text is ignored (`FSc (Pre-Medical)`).
- **Geography:** province aliases (`KPK/KP/Khyber Pakhtunkhwa`, `Baluchistan`, `AJK/Azad Kashmir`,
  `GB`, `ICT/Islamabad`). Domicile is **never inferred from province**; a district compared with a
  province is unknown. `All Pakistan`/`Nationwide` = unrestricted. Destinations: `USA/US/United States`,
  `UK/United Kingdom`, `KSA`, `UAE`, `Turkey/Turkiye`; `England`, `America`, `Britain`, `Aus`,
  `Emirates` are **not** aliased; regions such as `Europe` are unknown.
- **Income:** PKR amounts (`Rs. 50,000`, `50,000/-`). Monthly-vs-annual mismatch or a non-PKR currency
  is unknown.
- **Status:** `open/active/available/ongoing` pass; `closed/expired/…` fail; `upcoming`, `not_announced`,
  `unknown`, `pending`, anything unrecognised is unknown (never eligible).
- **Deadline:** parsed ISO, `dd/mm/yyyy`, `15 June 2031`, `June 15, 2031`, `15th June 2031`, datetimes.
  Ambiguous (`03/04/2031`), multi-date (`open … close …`), `TBA`/`Rolling`, impossible dates are unknown.
  A deadline of today is still open; past is `ineligible`; missing is unknown.

## Matching
```python
from src.matching import find_matches
matches = find_matches({"age": 20, "province": "Punjab", "marks": 80}, limit=50)   # today=... optional
```
Each item has: `id, name, provider, description, education_level, location, funding, deadline, status,
application_url, source_url, verified_at, eligibility_status, reasons, missing_information,
record_type, diagnostics`.

- `record_type == "scholarship"`: a real record. `diagnostics` lists problems evaluating it (e.g. the
  eligibility check raised) — such a record is `insufficient_information`, not hidden.
- `record_type == "error"`: placeholder for a row that could not be read. `eligibility_status` is
  `"error"`, scholarship fields are `None`, `error`/`diagnostics` explain. Error items come **after**
  real results and do not count toward `limit`. Filter on `record_type` in your UI.
- Ranking: `eligible` > `potentially_eligible` > `insufficient_information`; then earliest parsed deadline
  (missing/unparseable last); then id. `ineligible` records are excluded.
- `limit` is clamped to 1–500; invalid values (0, negative, text, bool) fall back to 50.
- A `None`/non-mapping profile is treated as empty (every result is then unknown, never eligible).
- **Database failures are never turned into `[]`:** initialisation, connection and query errors raise
  `RuntimeError`. An empty list means the table really has no eligible/unknown rows.

## Tests
```bash
python -m pytest -q
python -m compileall -q src tests
```
- 247 tests in `tests/` (parametrised cases counted individually). `tests/conftest.py` redirects every
  test to a temporary SQLite file and fails any test that creates or changes `data/scholarships.db`.
  Tests use synthetic workbooks only; none reads or writes your real database or workbook.

## Known limitations
- Narrative rules in curated workbooks (e.g. `nationality_rule`, `domicile_rule`, `other_eligibility`,
  `minimum_grade`, `income_threshold`) are **not** converted to structured criteria; imported records
  carry them in `raw_data` only, so matching reports those criteria as unknown.
- Field of study is compared as text (exact; partial overlap is unknown). No synonym handling.
- Dedup is application-level (no UNIQUE constraint); concurrent importers are serialised by SQLite's
  write lock, but the database does not itself forbid duplicates.
- Re-importing a workbook whose rows changed *and* have an incomplete key inserts the changed rows as new
  records (only exact repeats are skipped).
- Not eligibility advice; no scraping, no authentication, no network access.
