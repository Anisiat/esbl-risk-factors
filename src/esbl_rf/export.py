"""Format, suppress and write the aggregate outputs that may leave the SDE (``outputs/export/``).

The only module that writes to ``paths.export_dir``. It reads aggregate
checkpoints only (never episode-level data) and applies ``esbl_rf.suppression``
to every count. Number formats (CLAUDE.md): ORs and 95% CIs to 2 decimal
places; percentages to 1. Tables report estimates with 95% CIs and no
p-values (decided 2026-11-25); the univariable screening p-value appears only
in the methods table ``model_selection``.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from esbl_rf.suppression import round_counts, show_count, suppress, suppress_counts
from esbl_rf.tables import GROUPS

log = logging.getLogger(__name__)

DASH = "–"
ROW_LEVEL_COLUMNS = {"subject", "infection_id", "spell_identifier", "surgery_event_id"}


# --- number formats -------------------------------------------------------

def fmt_or(value: float, low: float, high: float) -> str:
    """``1.23 (0.98–1.54)``; a dash if not estimated."""
    if pd.isna(value):
        return DASH
    return f"{value:.2f} ({low:.2f}–{high:.2f})"


def fmt_p(p: float) -> str:
    """p-value to 3 decimal places, ``<0.001`` below that; a dash if missing."""
    if pd.isna(p):
        return DASH
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def median_iqr_decimals(values: list[float]) -> int:
    """Decimal places for one Table 1 row: none if every value is whole or any is 100 or more, else one."""
    shown = [v for v in values if pd.notna(v)]
    whole = all(float(v).is_integer() for v in shown)
    return 0 if whole or any(abs(v) >= 100 for v in shown) else 1


def fmt_median_iqr(median: float, q1: float, q3: float, dp: int | None = None) -> str:
    """``median [q1–q3]``; ``dp`` decimal places (default from ``median_iqr_decimals``)."""
    if dp is None:
        dp = median_iqr_decimals([median, q1, q3])
    return f"{median:.{dp}f} [{q1:.{dp}f}–{q3:.{dp}f}]"


def var_label(cfg: dict, name: str) -> str:
    return cfg["labels"]["variables"].get(name, name)


def level_label(cfg: dict, level) -> str:
    if pd.isna(level):
        return ""
    return cfg["labels"]["levels"].get(str(level), str(level))


# --- Table 1 --------------------------------------------------------------

def table1_display(t1: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, list[str]]:
    """Formatted, suppressed Table 1 (or its supplement).

    Grain: one display row per block header, variable and categorical level.
    Columns: Characteristic | Missing | Overall | ESBL-positive | Non-ESBL. Linked
    sets for secondary suppression: each row (overall = ESBL-positive +
    non-ESBL) and, per group, the levels of a categorical variable (with the
    overall missing count, since levels + missing = N).
    """
    thr = cfg["suppression_threshold"]
    t1 = t1.reset_index(drop=True)
    count_cols = [f"{g}_n" for g in GROUPS] + ["overall_missing"]
    values = t1[count_cols].astype(float)
    # missing is shown once per variable, on its first row
    values.loc[t1["variable"].duplicated(), "overall_missing"] = np.nan
    values.loc[t1["kind"] == "total", "overall_missing"] = np.nan
    counted = t1["kind"].isin(["categorical", "binary"])
    values.loc[~counted, [f"{g}_n" for g in GROUPS]] = np.nan   # continuous n is not shown
    values.loc[t1["kind"] == "total", [f"{g}_n" for g in GROUPS]] = t1.loc[t1["kind"] == "total", [f"{g}_n" for g in GROUPS]].astype(float)

    sets = [[(i, f"{g}_n") for g in GROUPS] for i in t1.index[counted]]
    for var, rows in t1[t1["kind"] == "categorical"].groupby("variable", sort=False).groups.items():
        for g in GROUPS:
            sets.append([(i, f"{g}_n") for i in rows] + ([(rows[0], "overall_missing")] if g == "overall" else []))
    primary, secondary = suppress(values, thr, sets)

    names = {g: f"{cfg['labels']['groups'][g]} (N={int(t1.loc[t1['kind'] == 'total', f'{g}_n'].iloc[0])})"
             for g in GROUPS}
    out = []
    for block, rows in t1[t1["kind"] != "total"].groupby("block", sort=False):
        out.append({"Characteristic": block})
        for var, vrows in rows.groupby("variable", sort=False):
            first, kind = vrows.index[0], vrows["kind"].iloc[0]
            missing = show_count(values.at[first, "overall_missing"], primary.at[first, "overall_missing"],
                                 secondary.at[first, "overall_missing"], thr)
            label = var_label(cfg, var)
            if kind == "continuous":
                r = vrows.iloc[0]
                row = {"Characteristic": f"{label}, median [IQR]", "Missing": missing}
                dp = median_iqr_decimals([r[f"{g}_{s}"] for g in GROUPS for s in ("median", "q1", "q3")
                                          if r[f"{g}_denom"] >= thr])
                for g in GROUPS:
                    small = 0 < r[f"{g}_denom"] < thr
                    row[names[g]] = (f"<{thr} values" if small else
                                     "" if r[f"{g}_denom"] == 0 else
                                     fmt_median_iqr(r[f"{g}_median"], r[f"{g}_q1"], r[f"{g}_q3"], dp))
                out.append(row)
                continue
            if kind == "binary":
                targets = [(first, f"{label}, n (%)")]
                out_missing = missing
            else:
                out.append({"Characteristic": f"{label}, n (%)", "Missing": missing})
                targets = [(i, f"  {level_label(cfg, t1.at[i, 'level'])}") for i in vrows.index]
                out_missing = ""
            for i, text in targets:
                row = {"Characteristic": text, "Missing": out_missing}
                for g in GROUPS:
                    c = f"{g}_n"
                    shown = show_count(values.at[i, c], primary.at[i, c], secondary.at[i, c], thr)
                    denom = t1.at[i, f"{g}_denom"]
                    if shown.isdigit() and denom:
                        shown += f" ({100 * t1.at[i, c] / denom:.1f})"
                    row[names[g]] = shown
                out.append(row)
    table = pd.DataFrame(out, columns=["Characteristic", "Missing", *names.values()]).fillna("")
    notes = ["Unit of analysis: infection episode; a patient may contribute more than one episode.",
             "Categorical variables: n (%), percentages of episodes with a recorded value. Continuous variables: "
             "median [interquartile range]. Missing: episodes with no recorded value (all groups).",
             "Prior hospital admission, prior antibiotics, prior ESBL-positive culture and comorbidities: absence "
             "of a record is taken as absence of the exposure.",
             "Descriptive; no statistical tests.",
             f"<{thr}: fewer than {thr} episodes; *: hidden so that a small count cannot be worked out from totals."]
    return table, notes


# --- Table 2 --------------------------------------------------------------

def table2_display(uni: pd.DataFrame, terms: pd.DataFrame, selection: pd.DataFrame, diagnostics: pd.DataFrame,
                   cfg: dict) -> tuple[pd.DataFrame, list[str]]:
    """Univariable OR, multivariable clustered aOR and first-episode aOR, by row block.

    Grain: one display row per block header, continuous/binary predictor,
    categorical variable header and non-reference level. A dash marks a
    variable not in the multivariable model. No counts, so no suppression.
    """
    refs = cfg["logistic_regression"]["reference_levels"]
    key = ["variable", "level"]
    def by_term(df):
        return df.assign(level=df["level"].fillna("")).set_index(key)
    u = by_term(uni)
    m = {name: by_term(terms[terms["model"] == name]) for name in ("primary", "first_episode")}
    cols = ["Variable", "Univariable OR (95% CI)", "Multivariable aOR (95% CI)", "First-episode aOR (95% CI)"]
    order = [v for block in cfg["table1"]["blocks"].values() for v in block if v in set(selection["variable"])]
    blocks = {v: b for b, vs in cfg["table1"]["blocks"].items() for v in vs}

    def cells(k):
        row = [fmt_or(*u.loc[k, ["odds_ratio", "ci_lower", "ci_upper"]])]
        for name in ("primary", "first_episode"):
            df = m[name]
            row.append(fmt_or(*df.loc[k, ["odds_ratio", "ci_lower", "ci_upper"]]) if k in df.index else DASH)
        return row

    out, current = [], None
    for var in order:
        if blocks[var] != current:
            current = blocks[var]
            out.append([current] + [""] * 3)
        levels = u.loc[var].index.tolist()
        if levels == [""]:
            out.append([var_label(cfg, var)] + cells((var, "")))
        else:
            out.append([f"{var_label(cfg, var)} (ref: {level_label(cfg, refs[var])})"] + [""] * 3)
            out += [[f"  {level_label(cfg, lvl)}"] + cells((var, lvl)) for lvl in levels]
    table = pd.DataFrame(out, columns=cols)

    d = diagnostics.set_index("model")
    thr = cfg["suppression_threshold"]
    def n_ev(model):
        return (f"{suppress_counts(pd.Series([d.at[model, 'n_used']]), thr)[0]} episodes, "
                f"{suppress_counts(pd.Series([d.at[model, 'events']]), thr)[0]} ESBL-positive")
    notes = ["OR: odds ratio; aOR: adjusted odds ratio; CI: confidence interval. Unit of analysis: infection "
             "episode. Estimates describe associations, not causal effects.",
             "Univariable: all episodes with a recorded value for that variable.",
             f"Multivariable: complete cases ({n_ev('primary')}), patient-clustered standard errors; "
             f"EPV {d.at['primary', 'epv']:.1f}.",
             f"First episode per patient: {n_ev('first_episode')}, ordinary standard errors; "
             f"EPV {d.at['first_episode', 'epv']:.1f}.",
             f"Candidates: univariable joint Wald p < {cfg['logistic_regression']['screen_p']} or clinically "
             "important (prespecified); every candidate is in both multivariable models. A dash: not in the "
             "multivariable model.",
             "Continuous variables per unit (age per year, IMD per decile, surgery duration per hour, CCI per point)."]
    merged = d.at["primary", "merged_levels"]
    if isinstance(merged, str) and merged:
        notes.append(f"Sparse levels merged before modelling: {merged}.")
    return table, notes


# --- codes and definitions (RECORD 6.1, 7.1) -----------------------------

def _join(values) -> str:
    return "; ".join(str(v) for v in values)


def _pattern_words(pattern: str) -> str:
    """Site regex as readable keywords: ``\\bwound\\b|\\bpus\\b`` -> ``wound, pus``."""
    return ", ".join(w.replace("\\b", "") for w in pattern.split("|"))


def definitions_table(cfg: dict) -> tuple[pd.DataFrame, list[str]]:
    """Supplementary table of the codes, lists and rules that define the cohort, outcome and variables.

    Built from ``config.yaml`` only (no data), so it always matches the code that
    produced the results. Grain: one row per definition.
    """
    st, se, ep = cfg["study"], cfg["sample_eligibility"], cfg["episodes"]
    oc, ex, pr, cl = cfg["outcome"], cfg["exposures"], cfg["prophylaxis"], cfg["clinical"]
    lr = cfg["logistic_regression"]
    rows = [
        ("Cohort", "Study period", f"Surgery start from {st['study_start']} to {st['study_end']} inclusive"),
        ("Cohort", "Age", f"{st['min_age']} years or older at admission"),
        ("Cohort", "Elective admission", "NHS admission method codes " + _join(st["elective_admission_methods"])),
        ("Cohort", "Surgery-to-admission linkage", "Surgery start between admission and discharge of the spell"),
        ("Samples", "Organisms", _join(level_label(cfg, o) for o in st["organisms"])),
        ("Samples", "Timing", f"Collected at least {se['min_hours_after_admission']} h after admission and no more "
                              f"than {se['max_days_after_surgery']} days after the most recent preceding surgery "
                              "(including after discharge)"),
        ("Samples", "Screening samples excluded",
         f"Culture codes {_join(se['screening']['culture_codes'])}; sites {_join(se['screening']['rectal_sites'])}"),
    ]
    words: dict[str, list[str]] = {}            # a label can have several patterns (body-location rules last)
    for g in cfg["sites"]["groups"]:
        words.setdefault(g["label"], []).append(_pattern_words(g["pattern"]))
    rows += [("Samples", f"Specimen site: {level_label(cfg, label)}", "Site or culture type contains: " + ", ".join(w))
             for label, w in words.items()]
    rows += [
        ("Samples", "Episode specimen site", "First present in the order "
         + _join(level_label(cfg, s) for s in cfg["sites"]["hierarchy"])),
        ("Episodes", "Infection episode", f"Positive cultures of the same organism no more than {ep['gap_days']} days "
                                          "apart; a different organism, or a gap of more than "
                                          f"{ep['gap_days']} days, starts a new episode"),
        ("Outcome", "ESBL-positive sample",
         f"Any of: a resistant result for {_join(oc['resistant_drugs'])}; a positive ESBL marker (confirmatory "
         "test); the laboratory's ESBL-producer statement. Result strings are classed resistant or susceptible "
         "by the lookup tables 3GCR and 3GCS; intermediate and 'susceptible with optimised dosing' count as "
         "susceptible"),
        ("Outcome", "Non-ESBL sample",
         f"No positive evidence and any of: susceptible to {_join(oc['susceptible_alone_drugs'])}; a negative "
         "ESBL marker" + "".join(f"; susceptible to both {a} and {b}" for a, b in oc["susceptible_pairs"])),
        ("Outcome", "Episode status", "ESBL-positive if any sample is ESBL-positive; non-ESBL if at least one "
                                      "sample is non-ESBL and none is positive; episodes with no status excluded"),
        ("Exposures", "Prior hospital admission", f"Inpatient admission in the {ex['lookback_days']} days before "
                                                  "the index admission"),
        ("Exposures", "Prior antibiotics", f"Secondary-care antibiotic administration 1 to {ex['lookback_days']} "
                                           "days before admission"),
        ("Exposures", "Prior ESBL-positive culture",
         "Any time before admission" if ex["prior_esbl_lookback_days"] is None else
         f"In the {ex['prior_esbl_lookback_days']} days before admission"),
        ("Exposures", "Absence of a record", "Taken as no exposure"),
        ("Exposures", "Perioperative prophylaxis window",
         f"IV antibacterials (route {_join(pr['iv_routes'])}) from {pr['hours_before_start']} h before surgery "
         f"start to {pr['hours_after_stop']} h after surgery stop; agents within {pr['combine_minutes']} min of "
         "the dose closest to surgery start form one course"),
    ]
    rows += [("Exposures", f"Prophylaxis agent class: {name.replace('_', ' ')}", _join(agents))
             for name, agents in pr["agent_classes"].items()]
    groups: dict[str, list[str]] = {}
    for tfc, group in cfg["tfc_groups"].items():
        groups.setdefault(group, []).append(tfc)
    rows += [("Exposures", f"Surgical specialty: {level_label(cfg, g)}", _join(sorted(v)))
             for g, v in groups.items()]
    eth: dict[str, list[str]] = {}
    for raw, group in cfg["demographics"]["ethnicity_groups"].items():
        eth.setdefault(level_label(cfg, group) if group else "Missing", []).append(raw)
    rows += [("Demographics", f"Ethnicity: {g}", _join(v)) for g, v in eth.items()]
    rows += [
        ("Comorbidity", "Charlson Comorbidity Index",
         f"comorbidicare: ICD-10 and SNOMED CT codes from diagnoses, problems and prescriptions recorded before "
         f"{cfg['cci']['cutoff']}; groups " + _join(level_label(cfg, g) for g in cfg["cci"]["group_labels"])),
        ("Clinical", "Temperature and CRP", f"Value closest to the first culture within {cl['window_hours']} h, in "
                                            "the spell and after surgery start; implausible values dropped "
                                            "(kept: temperature {}–{} °C, CRP {}–{} mg/L)".format(
                                                *cl["temperature"]["plausible"], *cl["crp"]["plausible"])),
        ("Clinical", "In-hospital death", "Discharge method code " + _join(cfg["mortality"]["died_codes"])
         + "; codes " + _join(cfg["mortality"]["unknown_codes"]) + " missing"),
        ("Analysis", "Variable selection", f"Univariable joint Wald p < {lr['screen_p']}, or prespecified as "
         "clinically important: " + _join(var_label(cfg, v) for v in lr["clinically_important"])),
        ("Analysis", "Sparse categories", f"Non-reference levels with fewer than {lr['sparse']['min_events']} "
                                          "ESBL-positive or non-ESBL episodes merged into Other"),
    ]
    notes = ["Generated from config.yaml, the configuration the analysis code ran with. Text values are matched "
             "after lower-casing."]
    return pd.DataFrame(rows, columns=["Domain", "Item", "Definition"]), notes


# --- subgroups, diagnostics, flowchart ------------------------------------

def subgroup_display(sub: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, list[str]]:
    """Table S3: subgroup ORs with n, events and EPV per stratum (counts suppressed)."""
    thr = cfg["suppression_threshold"]
    strata = sub.drop_duplicates(["subgroup", "stratum"])[["subgroup", "stratum", "status", "n", "events", "epv"]]
    out = []
    for r in strata.itertuples():
        non_events = r.n - r.events
        hide_events = 1 <= r.events < thr or 1 <= non_events < thr
        stratum = f"{var_label(cfg, r.subgroup)}: {level_label(cfg, r.stratum)}"
        base = {"Stratum": stratum, "Status": r.status, "n": suppress_counts(pd.Series([r.n]), thr)[0],
                "Events": (f"<{thr}" if 1 <= r.events < thr else "*" if hide_events else f"{int(r.events)}"),
                "EPV": "" if pd.isna(r.epv) else f"{r.epv:.1f}"}
        rows = sub[(sub["subgroup"] == r.subgroup) & (sub["stratum"] == r.stratum) & sub["variable"].notna()]
        if rows.empty:
            out.append({**base, "Variable / category": DASH, "aOR (95% CI)": DASH})
            continue
        for t in rows.itertuples():
            name = var_label(cfg, t.variable) + (f": {level_label(cfg, t.level)}" if pd.notna(t.level) else "")
            out.append({**base, "Variable / category": name,
                        "aOR (95% CI)": fmt_or(t.odds_ratio, t.ci_lower, t.ci_upper)})
    cols = ["Stratum", "Variable / category", "aOR (95% CI)", "n", "Events", "EPV", "Status"]
    lr = cfg["logistic_regression"]
    notes = ["Exploratory; adjusted for the primary-model predictors (the stratifying variable excluded). "
             "EPV: ESBL-positive or non-ESBL episodes, whichever is fewer, per model parameter.",
             f"Strata with EPV < {lr['min_subgroup_epv']} are not estimated; EPV < "
             f"{lr['stable_subgroup_epv']} is flagged as unstable. Patient-clustered standard errors.",
             f"<{thr}: fewer than {thr}; *: hidden so that a small count cannot be worked out."]
    return pd.DataFrame(out, columns=cols), notes


def diagnostics_display(diag: pd.DataFrame, vif: pd.DataFrame, selection: pd.DataFrame, cc: pd.DataFrame,
                        merged: pd.DataFrame, cfg: dict) -> dict[str, pd.DataFrame]:
    """Model diagnostics with counts suppressed (complete-case loss rounded)."""
    thr, base = cfg["suppression_threshold"], cfg["rounding_base"]
    d = diag.copy()
    for c in ("n_episodes", "n_used", "events", "non_events", "n_clusters"):
        d[c] = suppress_counts(d[c], thr)
    d["epv"] = d["epv"].round(1)
    d["max_vif"] = d["max_vif"].round(2)
    c = cc.copy()
    for col in ("n_missing", "n_dropped", "n_remaining"):
        c[col] = round_counts(c[col], thr, base)
    m = merged.copy()
    for col in ("events", "non_events"):
        m[col] = suppress_counts(m[col], thr)
    s = selection.assign(joint_p=selection["joint_p"].map(fmt_p))
    return {"model_diagnostics": d, "model_vif": vif.assign(vif=vif["vif"].round(2)), "model_selection": s,
            "model_complete_case": c, "model_merged_levels": m}


def flowchart_display(flow: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, list[str]]:
    """Flowchart with every count rounded to ``rounding_base`` (1 to threshold - 1 shown as <threshold)."""
    thr, base = cfg["suppression_threshold"], cfg["rounding_base"]
    out = flow[["order", "source", "description", "informational"]].copy()
    for col in [c for c in flow.columns if c.startswith("n_") or c.endswith("_excluded")]:
        out[col] = round_counts(flow[col], thr, base)
    notes = [f"All counts rounded to the nearest {base}; exclusions rounded separately, so boxes may not add up "
             f"exactly. <{thr}: 1 to {thr - 1}."]
    return out, notes


# --- forest plot ----------------------------------------------------------

def forest_plot(terms: pd.DataFrame, cfg: dict, stem: Path) -> list[Path]:
    """Forest plot of primary-model aORs (log scale); vector PDF and 300 dpi PNG.

    Estimates whose 95% CI excludes 1 are drawn darker (no p-values shown).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    refs = cfg["logistic_regression"]["reference_levels"]
    t = terms[terms["model"] == "primary"].reset_index(drop=True)
    labels, ys, rows, headers, y = [], [], [], [], 0
    for var, rows_v in t.groupby("variable", sort=False):
        if rows_v["level"].notna().any():
            labels.append(f"{var_label(cfg, var)} (ref: {level_label(cfg, refs[var])})")
            headers.append(len(labels) - 1)
            ys.append(y); y += 1
            for i, r in rows_v.iterrows():
                labels.append(f"   {level_label(cfg, r['level'])}"); ys.append(y); rows.append((y, r)); y += 1
        else:
            r = rows_v.iloc[0]
            labels.append(var_label(cfg, var)); ys.append(y); rows.append((y, r)); y += 1

    fig, ax = plt.subplots(figsize=(8, max(4, 0.32 * len(labels) + 1)))
    for yy, r in rows:
        sig = r["ci_lower"] > 1 or r["ci_upper"] < 1
        colour = "#1f3a93" if sig else "0.55"
        ax.plot([r["ci_lower"], r["ci_upper"]], [yy, yy], color=colour, lw=1.6)
        ax.plot(r["odds_ratio"], yy, "o", color=colour, ms=5 if sig else 4)
    ax.axvline(1, color="black", ls="--", lw=0.8)
    ax.set_xscale("log")
    low, high = t["ci_lower"].min(), t["ci_upper"].max()
    ticks = [x for x in (0.05, 0.1, 0.25, 0.5, 1, 2, 4, 10, 20) if low / 1.5 <= x <= high * 1.5]
    ax.set_xticks(ticks)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:g}"))
    ax.xaxis.set_minor_locator(mticker.NullLocator())
    ax.set_yticks(ys)
    ax.set_yticklabels(labels, fontsize=8)
    for i in headers:
        ax.get_yticklabels()[i].set_fontweight("bold")
    ax.invert_yaxis()
    ax.set_xlabel("Adjusted odds ratio (95% CI, log scale)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title("Factors associated with ESBL positivity (blue: 95% CI excludes 1)", fontsize=10)
    fig.tight_layout()
    paths = [stem.with_suffix(".pdf"), stem.with_suffix(".png")]
    fig.savefig(paths[0])
    fig.savefig(paths[1], dpi=300)
    plt.close(fig)
    return paths


# --- writing --------------------------------------------------------------

def check_aggregate(name: str, df: pd.DataFrame) -> None:
    """Refuse to export a table with a row-level identifier column."""
    bad = ROW_LEVEL_COLUMNS & set(df.columns)
    if bad:
        raise ValueError(f"export: table '{name}' has row-level column(s) {sorted(bad)}; not exported")


def write_tables(tables: dict[str, tuple[pd.DataFrame, list[str]]], export_dir: Path) -> list[Path]:
    """Write each table as CSV and all of them as sheets of ``tables.xlsx`` (notes under each table)."""
    from openpyxl.styles import Font

    export_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, (df, _) in tables.items():
        check_aggregate(name, df)
        path = export_dir / f"{name}.csv"
        df.to_csv(path, index=False)
        written.append(path)
    xlsx = export_dir / "tables.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        for name, (df, notes) in tables.items():
            sheet = name[:31]
            df.to_excel(writer, sheet_name=sheet, index=False)
            ws = writer.sheets[sheet]
            for cell in ws[1]:
                cell.font = Font(bold=True)
            for i, note in enumerate(notes):
                ws.cell(row=len(df) + 3 + i, column=1, value=note).font = Font(italic=True, size=9)
            for col in ws.columns:
                width = max(len(str(c.value)) for c in col[: len(df) + 1] if c.value is not None) if len(df) else 10
                ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 8), 60)
    written.append(xlsx)
    return written
