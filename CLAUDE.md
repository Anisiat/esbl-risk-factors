# CLAUDE.md — esbl-risk-factors

Factors associated with ESBL positivity among postoperative Enterobacterales
infection episodes in adult elective surgery. Target: high-impact clinical
journal (venue undecided — keep all outputs journal-agnostic and
reproducible to a standard suitable for code release).

## Working rules (read first)

- **Plan first, then ask before every file change.** Propose a plan, wait for
  approval, and confirm before creating, editing, moving or deleting any file.
- **Never change study definitions** (cohort, episode, outcome, prophylaxis
  grouping, exposure windows) without asking. If code and this file disagree,
  stop and flag it.
- **Never guess a column name.** Check `docs/data_dictionary.xlsx` (local
  only: `*.xlsx` is git-ignored, so a fresh clone will not have it). If a
  column is not there, ask.
- **Never invent results, counts or estimates.** Real results exist only in the
  SDE. This repo only ever contains synthetic data.
- **`legacy/` is read-only reference.** Port logic from it deliberately; never
  import from it. When porting, state what changed and why.
- Prefer small, reviewable changes. One task per session.

## Study design

Retrospective multicentre cohort study, Imperial College Healthcare NHS Trust
(Charing Cross, Hammersmith, St Mary's), using iCARE linked EHR + microbiology
data. Within the cohort, the analytic comparison is ESBL-positive vs non-ESBL
infection episodes (case-case comparison; uninfected patients are not a
comparator group).

- **Population:** adults (≥18) with elective surgical admissions
  (admission method 11, 12, 13), study window `config.yaml: study_start` to
  `study_end` (2015-01-01 to 2025-09-30; decided 2026-11-12, the manuscript
  SQL used surgery stop ≤ 2025-08-31).
- **Eligible samples:** non-screening clinical specimens, collected
  postoperatively, ≥48 h after admission, and no more than
  `config.yaml: sample_eligibility.max_days_after_surgery` (30) days after the
  most recent preceding surgery (decided 2026-11-12). The manuscript used
  "up to 30 days after discharge"; this is a deliberate CHANGE. Samples
  collected after discharge are included if they fall inside that window
  (decided 2026-11-16; SQL `04_cohort_micro.sql` links them). The microbiology
  table has no location column, so post-discharge samples cannot be split by
  setting; `collected_during_admission` marks in-hospital samples.
- **Organisms:** E. coli, K. pneumoniae, K. oxytoca, P. mirabilis only.
- **Infection episode:** same organism within 14 days = same episode; new
  episode if different organism or same organism >14 days after the previous
  positive culture. Patients can contribute >1 episode.
- **Outcome:** episode is ESBL-positive if any isolate shows cefpodoxime
  resistance, a positive ESBL confirmatory test, or resistance to ≥1
  third-generation cephalosporin. Non-ESBL otherwise (phenotypic definition).
- **Exposures:** age, sex, ethnicity, IMD decile; Charlson Comorbidity Index
  (via `comorbidicare`) + individual conditions for descriptive tables;
  hospitalisation / emergency admission and secondary-care antibiotics in the
  90 days before admission; prior ESBL-positive culture; surgical specialty,
  surgery duration, perioperative prophylaxis group; organism; specimen site.
  Absence of a record = no recorded exposure.
- **Analysis:** univariable logistic regression (p<0.20 or clinically
  important → candidate); multivariable logistic regression with
  patient-clustered SEs (primary); sensitivity: first episode per patient;
  exploratory subgroups by specialty and specimen site. Complete-case. Report
  EPV and VIFs. Sparse categories collapsed per `config.yaml`.

## Data model and known pitfalls

- `icare_episodes_anon` is **episode-level** (several rows per spell). State
  the grain of every dataframe in its docstring and assert key uniqueness
  after every join.
- Surgery-to-spell linkage: surgery must fall within admission → discharge.
  Specialty is taken from the episode containing the surgery, not "first" per
  spell.
- Dates arrive as strings (including `'none'`); parse with
  `pd.to_datetime(..., errors="coerce")` and count what failed to parse.
- The column name `treatment_function_code_des` is truncated in the source
  (`_des`, not `_desc`) — use it exactly. Its values are grouped into
  specialties by `config.yaml: tfc_groups`.
- `icare_microbiology_anon` has no `encntr_id`; link by `subject` + time.

## SDE workflow (critical)

Code is written locally on macOS, zipped and uploaded to the iCARE SDE,
then run as Python scripts in Azure ML via VS Code. **Every round trip is
slow — optimise for correct-first-time runs.**

1. SQL in `sql/` is run manually in a Snowflake worksheet and exported to CSV.
   Every SQL file starts with a header stating its output CSV filename; Python
   reads exactly those names from `config.yaml`.
2. `python scripts/make_bundle.py` creates `dist/esbl_rf_<githash>.zip`
   (code + config only, no data). Refuse to bundle with uncommitted changes.
3. In the SDE: unzip, `pip install -r requirements.txt` (via Nexus), then
   `python scripts/run_all.py`.
4. Every run writes `outputs/run_log.txt` with git hash, timestamp, package
   versions and row counts at each step.

Environment: Python <!-- TODO version -->. All dependencies pinned in
`requirements.txt`; local env must match. Ask before adding any package.

## Disclosure control

- Only `outputs/export/` is intended to leave the SDE. It contains aggregate
  tables and figures only — never row-level data.
- Every export function applies small-number suppression automatically:
  counts below `config.yaml: suppression_threshold` (10) are
  shown as `<N`, with secondary suppression where a suppressed cell could be
  back-calculated from totals.
- Flowchart counts, Table 1, model tables and subgroup tables all go through
  the same suppression helper.

## Code organisation

```
config.yaml            # dates, paths, CSV names, reference categories, thresholds
sql/                   # Snowflake queries (run manually, export to CSV)
src/esbl_rf/           # cohort, episodes, outcome, exposures, cci, models, tables, export
scripts/run_all.py     # entry point; --from <step> / --only <step>
scripts/make_bundle.py # builds the SDE upload zip
synthetic/             # generator + schema-matching fake CSVs
tests/                 # end-to-end synthetic run
docs/                  # data dictionary, analysis plan
legacy/                # old scripts, reference only
baseline/manuscript_v1/ # frozen copy of the SDE code behind the manuscript (read-only)
```

Work is in two phases: phase 1 ports the manuscript pipeline from
`baseline/manuscript_v1/` into `src/esbl_rf/` with tests; phase 2 applies the
planned changes (comorbidicare CCI, new SQL cohort) one at a time. The new code
reads the CSVs exported by the new SQL pipeline (`sql/pipeline/00-10`, decided
2026-11-13), not the baseline's old SQL outputs; the first flow-diagram counts
come from that pipeline's `qa_flowchart_sql.csv`. The cohort may differ slightly
from the manuscript's; that is accepted. Surgery anchor is surgery start.

**Build principle: streamline the baseline, do not redesign it.** The new code
is built from the baseline code, reusing the same functions and logic, and
keeping it simple. The aim is a cleaned-up version, not a new architecture.
When porting each function:

- drop functions the pipeline does not use;
- split functions that are too long or do several jobs;
- make fragile code robust (explicit checks, no silent failures, no reliance
  on row order or string parsing where a proper type exists);
- move hard-coded paths, dates, thresholds and category lists into
  `config.yaml`;
- keep the original function name and behaviour where it works, and state
  what changed and why.

The structure is built one file at a time, together, with the reasoning
explained at each step. Phase 1 should reproduce the baseline's results except
for the changes listed in the analysis plan.

- Each step is a function `run_<step>(cfg) -> DataFrame` that reads the
  previous checkpoint, writes `data/interim/<step>.csv`, logs row counts
  and asserts its own invariants. CSV, not parquet (pyarrow is not in the SDE
  pins); every load goes through `esbl_rf.io.read_checkpoint`, which restores
  dates and counts parse failures.
- No hardcoded paths, dates or categories — everything comes from
  `config.yaml`.
- Type hints and NumPy-style docstrings, including the dataframe grain.
- Fail loudly: clear `assert` / `ValueError` messages naming the step, the
  check and the offending counts.

## Testing (light, but always)

Before any bundle is made:

1. `python synthetic/generate.py` produces CSVs matching the real export
   schemas (column names and dtypes from the data dictionary, including
   messy cases: `'none'` dates, duplicate episodes, multi-episode spells).
2. `pytest tests/` runs the full pipeline end to end on synthetic data and
   checks that every output file is produced.
3. In-pipeline assertions cover key uniqueness, non-empty outputs, expected
   columns and outcome in {0, 1}.

Do not report a task as done until the synthetic end-to-end run passes.

## Reporting outputs (journal-agnostic)

- Follow STROBE and RECORD (routinely collected data).
- Flowchart counts generated automatically at every exclusion step.
- Table 1 by ESBL status with missingness column and **no p-values**
  (descriptive; CCI as median [IQR] and 0 / 1–2 / ≥3, a subset of Charlson
  conditions in the main table, all 17 in a supplementary table); Table 2 with univariable
  OR, multivariable aOR (clustered) and first-episode aOR; forest plot;
  subgroup table with n, events and EPV.
- ORs and 95% CIs to 2 decimal places; p-values to 3 decimal places, `<0.001`
  below that.
- Tables exported as both CSV and formatted .docx/.xlsx; figures as vector
  PDF + 300 dpi PNG.


Study definitions and table specifications are in docs/analysis_plan.md. Read it before any task touching the cohort, variables or outputs.


## Learning goals

This project is also a learning experience. I want to become a stronger
research software engineer and epidemiologist, not just get the code written.

- **Correct my terminology.** If I use a term loosely or wrongly (statistical,
  epidemiological, software or git), point it out briefly and give the
  correct term.
- **Explain the concepts behind your choices.** When you introduce a pattern,
  method or tool I may not know (e.g. cluster-robust SEs, secondary
  suppression, parquet checkpoints, dependency pinning), add a short
  explanation of what it is and why it fits here.
- **Teach current best practice.** Say when something is the modern standard
  versus an older habit, and when there's a real trade-off. Point to
  authoritative sources (official docs, STROBE/RECORD, statsmodels docs) where
  useful.
- **Be brief and targeted.** Explain each concept the first time it comes up
  in a session, not every time. Keep explanations in your messages; code
  comments stay focused on what the code does.
- **Don't oversimplify.** Precision over reassurance. If I'm about to do
  something that's statistically or technically wrong, say so plainly.