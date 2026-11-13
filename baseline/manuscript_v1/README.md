# baseline/manuscript_v1 — frozen reference copy of the manuscript pipeline

**Read-only reference. Do not edit, and never import from it in `src/esbl_rf/`.**
Port logic deliberately and state what changed and why.

This is the code that produced the manuscript results in the iCARE SDE
(Anisia Talianu, Comsmed submission), re-created locally from SDE screenshots
on 2026-11-11/12. Git tag `manuscript-sync-2026-11-12` marks the commit it was
copied from. Line counts were checked against `wc -l` in the SDE; transcription
from screenshots cannot guarantee character-level identity.

## Contents

| Folder | What |
|---|---|
| `sql/` | Snowflake pulls (run manually, export to CSV). Not the new `sql/pipeline/`. |
| `src/steps`, `src/utils` | Cleaning, episode building, exposures, LR helpers |
| `configs/` | `config.yaml`, `tfc.yaml`, `antibiotics.yaml`, `problems_mapping.yaml` |
| `scripts/` | The four manuscript scripts |
| `requirements.txt` | SDE package pins |

## Running it (SDE)

The folder is self-contained: `PROJECT_ROOT` is this folder, so raw data must be
at `baseline/manuscript_v1/data/`. Link the existing data rather than copying it:

```bash
cd baseline/manuscript_v1
ln -s ../../data data
python scripts/clean_and_save_dfs.py
python scripts/get_table1.py
python scripts/get_missingness_univariable.py --cluster-by-patient
python scripts/get_missingness_multivariable.py --cluster-by-patient
python scripts/get_missingness_multivariable.py --episodes first
```

The univariable step writes `data/processed/lr_data.csv`, which both
multivariable runs read, so keep this order.

## Deliberate deviations from the SDE code (all of them)

1. `scripts/clean_and_save_dfs.py`: `infection_eps.csv` is written to
   `DATA_DIR/processed/` (with `mkdir`) instead of the hard-coded
   `../../esbl_risk_factors/data/processed/`. Same file, no dependence on the folder name.
2. `src/steps/get_colonisation.py`: `get_prior_esbl_culture(window_days=None)` —
   prior ESBL means **ever** before admission, as in the manuscript run.
   A window can still be passed.
3. `configs/*.yaml` and `config.yaml` paths: unchanged apart from being loaded
   relative to the YAML by `load_config` (earlier local change).

4. `scripts/clean_and_save_dfs.py` line 99: one space removed
   (`cfg_path = cfg_path )` -> `cfg_path = cfg_path)`). Whitespace only, no effect on results.

Everything else, including known bugs, is deliberately left as it was.

## Known differences between this code and the manuscript

Found by comparing the manuscript text and tables with this code. Not fixed here.

- `filter_nosocomial` keeps samples up to 30 days **after discharge**; Methods says within the spell.
- Samples (not patients) are dropped for <48 h or pre-op; `unknown` ESBL isolates and `other`-site samples are dropped before episodes are built.
- New episode needs a gap of >=15 days (`dt.days` truncation); Methods says ">14", Figure S1 says "<14" and ">=14".
- Comorbidities are not restricted to "present on admission", are partly inferred from prescriptions, and those with <10% prevalence are dropped; none of this is in Methods.
- Multivariable script hard-codes dropping asthma, COPD, hypertension, days-to-first-culture and CRP, contrary to the "p<0.20 or clinically important" rule.
- Emergency exposure is ED attendance, not admission; config has no emergency row in Table 1 although the manuscript Table 1 does.
- `get_table1.py` sets `pval=False` but manuscript Table 1 has p-values; Table 1 "site missing 399" cannot arise because `other` sites are dropped upstream.
- Table 2 univariable ORs do not match crude ORs implied by Table 1 counts; Table 2 gender row is labelled "Male" but gender is coded female = 1.
- Prophylaxis rules (IV, -2 h/+1 h of surgery start, +/-6 h fallback; any cefuroxime-containing regimen = cefuroxime group), episode-level site hierarchy and specialty (mode over samples) are not described in Methods.
- Study window: SQL has no 2015 lower bound and ends 2025-08-31; Methods says to September 2025.
