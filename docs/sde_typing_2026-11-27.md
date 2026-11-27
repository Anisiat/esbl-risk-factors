# SDE typing sheet: 2026-11-27 (everything on main since bundle 8ed8f07's hand edits)

Code lines only (no docstrings, no tests). Indentation matters: copy it exactly.
Unchanged lines around each edit are given so you can find the spot.

**Progress in the SDE (from screenshots):**
- [x] 1. tables.py
- [ ] 2. surgery.py
- [~] 3a. config.yaml: keep list done (fix the typo `trimethroprim` → `trimethoprim`).
  The two status lines were added under `clinical: temperature:` and spelt
  `not_givem_statuses`: delete them there and add them under `antibiotics:` (see 3a).
- [ ] 3b. antibiotics.py
- [ ] 4. models.py
- [ ] 7. lookups/3GCS.csv (new, from the "Cefpodoxime lookup missing entries" session)
- [ ] 8. models.py zero-event guard (new, from the "Folder in SDE next steps" session)
- [ ] 9. keep 'other' site episodes + new site patterns + flowchart reset: config.yaml, steps.py, run_all.py
- [ ] 10. why is tfc missing: config.yaml, surgery.py, steps.py (or run the quick check in 10d first, no typing)
- [ ] 11. univariable 'not estimable' instead of stopping (models.py); definitions text (export.py, optional); cefalexin query (SQL, only if re-run)
- [ ] SQL `00_params.sql` / `AT_PARAMS` git_hash: set to the main commit after this round

---

## 1. Charlson table S1: `src/esbl_rf/tables.py` (1 line)

Find:
```python
            elif var in t1["binary"]:
```
Change to:
```python
            elif var in t1["binary"] or var in t1["supplementary_conditions"]:
```

---

## 2. Surgery count: `src/esbl_rf/surgery.py` (2 lines become 3)

Find:
```python
    n_before = pairs.groupby("_row").size()
    latest = pairs.sort_values(anchor, kind="stable").groupby("_row").tail(1)
```
Change to:
```python
    n_before = pairs.groupby("_row")["surgery_event_id"].nunique()
    latest = pairs.sort_values([anchor, "surgery_event_id", cols["procedure"]], kind="stable",
                               na_position="first").groupby("_row").tail(1)
```

---

## 3. Antibiotics

### 3a. `config.yaml`, section `antibiotics:` (always type this)

Find the `keep:` line and replace it with:
```yaml
  keep: [doxycycline, lymecycline, minocycline, oxytetracycline, tigecycline, sulfadiazine, metronidazole,
         co-trimoxazole, sulfamethoxazole, trimethoprim-sulfamethoxazole, tetracycline]
```
In the `antibiotics:` section (NOT `clinical:`), directly under
`time_col: administration_ts`, add:
```yaml
antibiotics:
  medication_col: medication_name_short
  route_col: route
  time_col: administration_ts
  status_col: administration_status      # <- new
  not_given_statuses: []                 # <- new (check the spelling: given)
```

### 3b. `src/esbl_rf/antibiotics.py`, in `filter_antibiotics` (type it together with 3a)

Find:
```python
    out = admin.copy()
    out[med] = _norm(out[med])
```
Insert the new block between those two lines:
```python
    out = admin.copy()
    if ab["not_given_statuses"]:
        status = ab["status_col"]
        if status not in out.columns:
            raise ValueError(f"filter_antibiotics: missing column '{status}'")
        not_given = _norm(out[status]).isin([v.lower() for v in ab["not_given_statuses"]]).fillna(False)
        log.info("filter_antibiotics: dropped %d administration(s) not given (%s)", int(not_given.sum()),
                 _norm(out.loc[not_given, status]).value_counts().to_dict())
        out = out[~not_given.to_numpy()]
    out[med] = _norm(out[med])
```
Then fill `not_given_statuses` from the run log's `'administration_status' values`
lines, lower case, e.g. `[not given, cancelled]` (those words are examples; use the
real ones).

(Optional: `src/esbl_rf/config.py` lists the required keys; skipping it changes nothing.)

---

## 4. Model fits: `src/esbl_rf/models.py` (4 places)

### 4a. `fit_logit`
Find:
```python
        result = sm.Logit(y.astype(float), exog).fit(**kwargs)
```
Change to:
```python
        try:
            result = sm.Logit(y.astype(float), exog).fit(**kwargs)
        except (np.linalg.LinAlgError, PerfectSeparationError) as err:
            return None, [f"fit failed: {err}"]
```

### 4b. New function: paste directly above `def joint_p_value`
```python
def not_estimable_terms(X: pd.DataFrame, model: str, y: pd.Series, cfg: dict) -> pd.DataFrame:
    refs = cfg["logistic_regression"]["reference_levels"]
    rows = []
    for term in X.columns:
        var, level = split_term(term)
        rows.append({"model": model, "variable": var, "level": level, "reference": refs.get(var) if level else None,
                     "odds_ratio": np.nan, "ci_lower": np.nan, "ci_upper": np.nan, "p_value": np.nan,
                     "n": len(y), "events": int(y.sum())})
    return pd.DataFrame(rows, columns=TERM_COLUMNS)


```

### 4c. `univariable`: replace these 6 lines
```python
        terms = tidy_terms(result, "univariable", data[outcome], cfg)
        terms["joint_p"] = joint_p_value(result, var)
        terms["problems"] = "; ".join(problems) or None
        if problems:
            log.warning("univariable[%s]: %s", var, problems)
        out.append(terms)
```
with:
```python
        if problems:
            log.warning("univariable[%s]: not estimable, no estimates reported: %s", var, problems)
            terms = not_estimable_terms(X, "univariable", data[outcome], cfg)
            terms["joint_p"] = float("nan")
        else:
            terms = tidy_terms(result, "univariable", data[outcome], cfg)
            terms["joint_p"] = joint_p_value(result, var)
        terms["problems"] = "; ".join(problems) or None
        out.append(terms)
```

### 4d. `multivariable`
Directly under
```python
    result, problems = fit_logit(y, X, data[lr["cluster_col"]] if clustered else None)
```
add:
```python
    if problems:
        raise ValueError(f"multivariable[{model}]: the model fit is not valid ({'; '.join(problems)})")
```
and delete these two lines a little further down:
```python
    if problems:
        log.warning("multivariable[%s]: %s", model, problems)
```

### 4e. `subgroups`: replace
```python
            try:
                result, problems = fit_logit(y, X, data[lr["cluster_col"]])
            except (np.linalg.LinAlgError, PerfectSeparationError) as err:
                out.append(pd.DataFrame([{**base, "status": f"not estimable: {err}"}]))
                continue
```
with:
```python
            result, problems = fit_logit(y, X, data[lr["cluster_col"]])
            if problems:
                out.append(pd.DataFrame([{**base, "status": "not estimable: " + "; ".join(problems),
                                          "problems": "; ".join(problems)}]))
                continue
```
and in the last line of that loop change `problems="; ".join(problems) or None` to `problems=None`.

---

## 5. Check after typing (type sections 7 to 11 below first)

```bash
python -c "import esbl_rf.models, esbl_rf.antibiotics, esbl_rf.surgery, esbl_rf.tables; print('ok')"
```
```bash
python scripts/run_all.py
```
(Run everything: fix 7 changes the ESBL labels, which is the first step, `label`.)

---

## 6. Optional: strict-outcome sensitivity run (no code)

```bash
cp config.yaml config_strict.yaml
```
In `config_strict.yaml` change only these lines:
```yaml
  susceptible_alone_drugs: [cefpodoxime]
  susceptible_pairs: [[cefotaxime, ceftazidime], [ceftriaxone, ceftazidime]]
```
```yaml
  interim_dir: data/interim_strict
  export_dir: outputs/export_strict
```
Then:
```bash
python scripts/run_all.py --config config_strict.yaml
```

---

## 7. Cefpodoxime / cefotaxime / cefixime susceptible rows: `lookups/3GCS.csv`

The new code matches whole `drug : result` strings; the baseline matched the result
word only, so it never needed one row per drug. Without these rows a cefpodoxime S
result matches nothing. Paste at the end of the file (keep the quotes on the long
lines, they contain a comma):
```
cefpodoxime : sensitive
cefpodoxime : susceptible
cefpodoxime : intermediate
"cefpodoxime : susceptible with optimised dosing, refer to antimicrobial policy"
cefotaxime : susceptible
"cefotaxime : susceptible with optimised dosing, refer to antimicrobial policy"
cefixime : susceptible
cefixime : intermediate
"cefixime : susceptible with optimised dosing, refer to antimicrobial policy"
```
Make sure the last existing line (`cephalexin : susceptible`) ends with a line break
before you paste, so the two lines don't join.

---

## 8. Zero-event guard: `src/esbl_rf/models.py` (branch `models/zero-event-guard`)

Stops the run with a clear message when a level has 0 events or 0 non-events
(the genital / site "other" problem, OR 0.00), instead of a meaningless estimate.

Section 9 (keep 'other' sites) is what lets the models run past this guard: genital
then merges into a real 'other' level that has ESBL episodes.

8a. Paste just above `def first_episode_per_patient(`:
```python
def check_both_outcomes(data: pd.DataFrame, predictors: list[str], cfg: dict, where: str) -> None:
    outcome, types = cfg["columns"]["outcome"], predictor_types(cfg)
    bad = []
    for var in predictors:
        if types[var] == "continuous":
            continue
        counts = pd.crosstab(data[var], data[outcome]).reindex(columns=[0, 1], fill_value=0)
        for lvl, row in counts[(counts[0] == 0) | (counts[1] == 0)].iterrows():
            bad.append(f"{var} = {lvl} ({row[1]} events, {row[0]} non-events)")
    if bad:
        raise ValueError(f"{where}: level(s) with no events or no non-events cannot be estimated "
                         f"(perfect separation): {bad}. Exclude these episodes or merge the level in config.yaml")


```
8b. In `univariable`, after `data = df.dropna(subset=[var])`:
```python
        check_both_outcomes(data, [var], cfg, f"univariable[{var}]")
```
8c. In `multivariable`, after `data = df.dropna(subset=predictors)`:
```python
    check_both_outcomes(data, predictors, cfg, f"multivariable[{model}]")
```

---

## 9. Keep 'other' site episodes; move recognisable sites into real groups

'Other' episodes (about 290) are no longer dropped. In the models 'other' is an
adjustment level, and genital (16 episodes, no ESBL) merges into it by the sparse
rule, so the OR 0.00 problem goes away. Genital keeps its own row in Table 1.

9a. `config.yaml`, section `sites: groups:`. Leave the existing patterns as they
are, and add three new lines directly under the `genital` line (they must come
LAST, so a specimen type such as drain, tissue or tip wins over a body location):
```yaml
    - {label: wound, pattern: '\babdomen\b|\babdominal\b'}
    - {label: drain, pattern: '\bbiliary\b'}
    - {label: sputum, pattern: '\bendotracheal\b'}
```
(If you already added these words inside the wound / drain / sputum patterns,
take them out again: there, 'abdominal drain' became wound and 'endotracheal tube
tip' became sputum.)

9b. `src/esbl_rf/steps.py`, in `run_episodes`, delete these two lines:
```python
    episodes = exclude_other_site_episodes(episodes, cfg)
    record_step(cfg, "episodes with a study specimen site", episodes, episode_col="infection_id", esbl_col=status_col)
```
(Leave the import and the function in `episodes.py` as they are; unused, harmless.)

9c. `scripts/run_all.py`: a full run never reset the flowchart log (it only reset
when the run *started* at `cohort`), so the removed step's row would stay. Change
```python
    if steps[0] == "cohort":
```
to
```python
    if "cohort" in steps:
```
Then run the whole pipeline (section 5). Check the run log line
`group_specimen_site: {...}`: 'other' should be smaller than before.

---

## 10. Why is tfc (specialty) missing?

Logs, per episode, whether a missing specialty means no description (empty or
`none`) or a description that is not in `tfc_groups` (with the names, so they can
be added to the mapping).

10a. `config.yaml`, section `episode_table: index_sample_columns:`, under `tfc: tfc_group`:
```yaml
    tfc_desc: treatment_function_code_des
```

10b. `src/esbl_rf/surgery.py`: paste at the end of the file:
```python


def check_specialty(episodes: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    desc_col = "tfc_desc"
    for col in ("tfc", desc_col):
        if col not in episodes.columns:
            raise ValueError(f"check_specialty: episodes is missing column '{col}'")
    missing = episodes[episodes["tfc"].isna()]
    desc = missing[desc_col].astype("string").str.strip().str.lower()
    empty = desc.isna() | desc.isin(["", "none"])
    reason = empty.map({True: "no description", False: "description not in tfc_groups"})
    out = (pd.DataFrame({"reason": reason, desc_col: desc.where(~empty)})
           .value_counts(dropna=False).rename("n_episodes").reset_index())
    if len(missing):
        log.warning("check_specialty: %d of %d episodes have no specialty: %d with no description, %d with a "
                    "description not in tfc_groups %s", len(missing), len(episodes), int(empty.sum()),
                    int((~empty).sum()), desc[~empty].value_counts().to_dict())
    else:
        log.info("check_specialty: every episode has a specialty")
    return out
```

10c. `src/esbl_rf/steps.py`, 2 places:
- In `from esbl_rf.surgery import ...`, add `check_specialty, ` after `attribute_surgery, `.
- In `run_episodes`, directly above
  `assert episodes["infection_id"].is_unique, "run_episodes: infection_id is not unique"` add:
```python
    check_specialty(episodes, cfg)
```
After the run, screenshot the `check_specialty:` line in the run log.

10d. Quick check now, without typing anything (sample level, from the last run's
checkpoint; paste in a Python cell from the repo folder):
```python
import pandas as pd
s = pd.read_csv("data/interim/eligible_samples.csv", usecols=["treatment_function_code_des", "tfc_group"], dtype=str)
m = s[s["tfc_group"].isna()]["treatment_function_code_des"].fillna("<empty>")
print(len(m), "of", len(s), "eligible samples have no specialty")
print(m.str.strip().str.lower().value_counts().head(20))
```

---

## 11. Review fixes, round 2

11a. `src/esbl_rf/models.py`, in `univariable`: a level with no events now gives
"not estimable" for that variable instead of stopping the run. Replace
```python
        check_both_outcomes(data, [var], cfg, f"univariable[{var}]")
        X, _ = design_matrix(data, [var], cfg)
        result, problems = fit_logit(data[outcome], X, data[lr["cluster_col"]])
```
with
```python
        X, _ = design_matrix(data, [var], cfg)
        try:
            check_both_outcomes(data, [var], cfg, f"univariable[{var}]")
            result, problems = fit_logit(data[outcome], X, data[lr["cluster_col"]])
        except ValueError as err:
            result, problems = None, [str(err)]
```
(`multivariable` still stops on such a level: leave it as typed in section 8c.)

11b. Optional, `src/esbl_rf/export.py`, in `definitions_table`: without it, the
definitions table lists wound, drain and sputum twice (once per pattern). Replace
```python
    rows += [("Samples", f"Specimen site: {level_label(cfg, g['label'])}",
              f"Site or culture type contains: {_pattern_words(g['pattern'])}") for g in cfg["sites"]["groups"]]
```
with
```python
    words = {}
    for g in cfg["sites"]["groups"]:
        words.setdefault(g["label"], []).append(_pattern_words(g["pattern"]))
    rows += [("Samples", f"Specimen site: {level_label(cfg, label)}", "Site or culture type contains: " + ", ".join(w))
             for label, w in words.items()]
```

11c. Only if you re-run `sql/exploration/03_urine_cefalexin.sql`: in statement 1,
the `NOT EXISTS (...)` must ignore cephalexin rows. Change its last line to:
```sql
                  WHERE r.LAB_TEST_ID = m.LAB_TEST_ID AND r.ORGANISM_BUG = m.ORGANISM_BUG
                    AND LOWER(TRIM(r.SENSITIVITY)) NOT LIKE 'cephalexin :%');
```
