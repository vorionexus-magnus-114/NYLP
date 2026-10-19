# 🎓 PathFinder AI — Pakistan Scholarship Finder

An offline-first scholarship discovery dashboard for Pakistani students. It combines a
curated Excel workbook (147 verified programme records), a deterministic eligibility engine
and an interactive Streamlit interface.

**The rule is simple:** a scholarship is only recommended when the recorded criteria can
actually be checked against the student's profile. A Sindh student never sees a
Khyber Pakhtunkhwa- or Punjab-only scheme, a bachelor's student never sees a PhD-only
scheme, and anything the source leaves vague is shown as *"confirm with the sponsor"*
instead of being silently marked eligible.

---

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

The app opens at <http://localhost:8501>. The database (`scholarships.db`) is committed,
so the first launch is instant; if it is missing it is rebuilt from
`Pakistan_Scholarship_Finder_Master.xlsx`.

Optional AI assistant:

```bash
export GEMINI_API_KEY=...          # see env.example
```

Without a key the assistant still works using deterministic, database-grounded replies.

Tests:

```bash
python -m pytest -q
python -m compileall -q src app.py
```

---

## What the app does

### 🏠 Dashboard
* Live metrics: **eligible now**, **likely eligible**, **needs information**, **filtered out**.
* Every widget in the sidebar updates the results immediately (there is no "reload" step).
* Charts for the eligibility split and where the matches are.
* Result cards with:
  * the exact criteria that passed (✅) and the ones that blocked the record (❌),
  * what is still unknown (⚠️) and what must be confirmed with the sponsor (🔎),
  * recorded requirements (level, province/domicile, grades, income, age, field, destination),
  * provenance (verification status, last checked, source publisher, evidence excerpt),
  * apply/source links and a save button.
* Optional "show scholarships I am **not** eligible for" mode with the blocking reason.

### 🔎 All Scholarships
Searchable and filterable catalogue of every record, with a `Your eligibility` column,
official links, CSV export and a full record detail panel.

### 🤖 Assistant
Scholarship-only chat. It extracts province, degree level, percentage/CGPA, field, age and
destination from natural language, updates the profile, and answers from the database only.
It never recommends an ineligible record and never invents deadlines or URLs.

### 👤 Profile
Full editable profile: education level, percentage **or** CGPA, field of study, province,
domicile, city, destination, age, gender, household income and its period, English test.
Both grading systems have their own input — switching between them reveals the matching
field (this was the old broken behaviour, now covered by tests).

### 🛡️ Data Health
Cross-checks the workbook itself:
* expired-but-open deadlines, open-without-deadline, closed-with-future-deadline,
* records not marked `confirmed` by the source review,
* stale verification dates, missing/invalid source URLs,
* grade or income rules that could not be converted,
* duplicate external ids and nationwide records with a domicile rule,
* full provenance table and CSV export,
* optional live re-verification with Gemini Search when a key is configured.

---

## How eligibility works

`src/criteria.py` converts the workbook's prose columns into structured criteria, and
`src/eligibility.py` evaluates them.

### Province and domicile
`province_region` is the primary source (`All Pakistan` = nationwide, otherwise the named
provinces). `domicile_rule` and `geographic_scope` are used when they contain a province
or when the scope is nationwide but a domicile is required.

* A province mismatch is a **hard fail** — this is what removes KP/Punjab schemes from a
  Sindh student's dashboard.
* Domicile is compared with the student's domicile when given. If it is not given, the
  province is used as a proxy and the assumption is reported in the result (`notes`) so it
  is never hidden.
* District lists (e.g. a Balochistan scheme targeting 11 districts) are matched against the
  student's city/district; a missing city is reported as unknown, not as a pass.

### Education level
`study_level` is parsed into a set of supported levels
(`matric`, `intermediate`, `vocational`, `bachelor`, `master`, `phd`):

* `"Nursery through BS"` → matric, intermediate, bachelor
* `"MS / MPhil / PhD (inland)"` → master, PhD
* `"Intermediate / DAE"` → intermediate, vocational
* `"Postdoctoral / lectureship"` → PhD (a bachelor's student is therefore excluded)
* Unparseable level descriptions (e.g. `"Research fellowship"`) stay unknown and produce a
  manual check instead of a guess.

### Grades
`minimum_grade` prose such as `"At least 60% or 3.0 GPA/CGPA"` becomes
`minimum_marks=60`, `minimum_cgpa=3.0`, `grade_logic="or"`.

* `or` means either path can satisfy the requirement; it fails only when **both** known
  values fail, and stays unknown when one side is missing.
* Percentage and CGPA are never converted into one another.
* `"85%"`, `"85 percent"`, `"900/1100"` are accepted from chat; the UI uses numeric inputs.

### Income
`income_threshold` is parsed into an amount plus a period (`month`/`year`, including
`PKR 1.5m`). Monthly and annual values are compared on a normalised monthly basis when the
student supplies a period; without one the comparison is reported as unknown.

### Field of study
`fields_of_study` is compared using field groups (computing, engineering, health, business,
law, environment, earth, media, design, education, science, arts, social):

* `"IT, engineering, medicine, business administration"` is an inclusive list → any
  recognised field matches.
* `"Cybersecurity"` matches Computer Science; `"European Forestry"` does not.
* Vague prose (`"Partner-specified eligible fields"`) stays unknown.

### Destination
The workbook's `destination_country` is used; when blank it is inferred from the record
(`Erasmus Mundus`/consortium → international, `overseas` in the name → international,
`.pk` source + local sponsor → Pakistan). A student who chooses **Abroad** is only shown
non-Pakistan opportunities, and vice versa. **Any** applies no destination filter.

### Everything else
* **Status:** `open`, `rolling`, `active`, `ongoing` count as open; `not_announced` is shown
  as "cycle not announced yet"; `closed`/`expired` are ineligible.
* **Deadline:** ISO, `dd/mm/yyyy`, `15 June 2031`, `June 15, 2031`, datetimes. Expired
  deadlines are ineligible; a deadline of today is still open.
* **Gender:** machine-checked when the source restricts it and the profile provides it.
* **Manual checks:** target beneficiary groups, employment conditions, language tests and
  similar source-only conditions never grant `eligible`; they cap the status at
  `potentially_eligible` until the student confirms.

### Status meanings

| Status | Meaning |
| --- | --- |
| `eligible` | Every recorded student criterion passed, and the cycle is open. |
| `potentially_eligible` | Criteria passed but something must be confirmed (unannounced cycle, missing deadline, manual condition). |
| `insufficient_information` | The profile or the source is missing data; nothing can be confirmed. |
| `ineligible` | A hard criterion failed (province, domicile, level, grade, income, field, destination, gender, status, deadline). |

---

## Project layout

```
app.py                               Streamlit UI (dashboard, catalogue, chat, profile, data health)
src/
  criteria.py                        prose → structured criteria (province, level, grade, income, field…)
  eligibility.py                     deterministic eligibility engine
  matching.py                        database evaluation, enrichment, ranking
  profile.py                          profile normalisation + natural-language extraction
  database.py                        schema, migrations, connection, seeding
  data_import.py                     workbook import (dedup, transactions, raw_data preservation)
  data_quality.py                    provenance / consistency audit
  ai_agent.py                        optional Gemini assistant + offline fallback
  scholarship_chat.py                topic guardrails and intent classification
  web_verification.py                optional cached Gemini source verification
scholarships.db                      SQLite database (147 records)
Pakistan_Scholarship_Finder_Master.xlsx
tests/                               pytest suite (criteria, eligibility, profile, matching, UI smoke)
```

## Data model

Every workbook row is kept verbatim in `raw_data` (JSON) in addition to the canonical
columns. The derived criteria are computed from `raw_data` at match time, so improving the
rules automatically improves every existing record without a re-import.

## Importing a newer workbook

```python
from src.data_import import import_workbook

result = import_workbook("path/to/workbook.xlsx")
print(result["records_inserted"], result["errors"], result["warnings"])
```

The importer skips TOC/metadata sheets, detects the real header row, deduplicates on
`external_id` and on `name + provider + deadline`, and imports each sheet in one
transaction. Ambiguous headers stay unmapped but are preserved in `raw_data` and consumed
by `src/criteria.py`.

## Known limitations

* Eligibility is **indicative**. Always confirm on the sponsor's page.
* 112 of 147 records have no confirmed deadline yet (the next cycle is not announced);
  they are shown as "likely eligible"/"needs information", never as "eligible now".
* Field-of-study matching is a curated synonym system, not semantic understanding.
* A domicile rule is compared with the province when no separate domicile is entered.
* Live source re-verification requires `GEMINI_API_KEY`; without it only the workbook's own
  provenance is shown.
