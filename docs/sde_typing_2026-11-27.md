# SDE typing sheet: 2026-11-27 (branch `review/fixes`)

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

## 5. Check after typing (type sections 7 and 8 below first)

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

**Until the genital decision is made (option 1: Table 1 only, out of the models;
or Firth), the models step will stop at this guard.** That is intended.

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
