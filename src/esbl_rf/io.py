"""Reading raw tables and lookups, parsing dates, and writing checkpoints."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import pandas as pd

log = logging.getLogger(__name__)

# Always read as text: 19-digit Snowflake HASH() IDs. As numbers they become floats as
# soon as a merge adds a missing value, and a float keeps only ~16 digits.
ALWAYS_TEXT = ("surgery_event_id",)


def parse_datetimes(series: pd.Series, name: str, step: str) -> pd.Series:
    """Parse a column of date strings and report what failed.

    Strings such as ``'none'`` and genuinely unparseable values become ``NaT``.
    ``format="mixed"`` parses each value on its own: with the default
    (infer the format from the first value, then coerce) a column that mixes
    formats silently turns every non-matching value into ``NaT``.

    Parameters
    ----------
    series : pd.Series
        Raw column (strings, datetimes or missing).
    name : str
        Column name, used in the log message.
    step : str
        Pipeline step, used in the log message.

    Returns
    -------
    pd.Series
        ``datetime64[ns]`` series, same index.
    """
    raw = series.astype("string").str.strip()
    parsed = pd.to_datetime(raw, errors="coerce", format="mixed")
    n_failed = int((parsed.isna() & raw.notna()).sum())
    n_blank = int(raw.isna().sum())
    if n_failed:
        log.warning("%s: column '%s': %d of %d value(s) could not be parsed (e.g. 'none')", step, name, n_failed, len(series))
    if n_blank:
        log.info("%s: column '%s': %d of %d value(s) already missing", step, name, n_blank, len(series))
    return parsed


def _read_csv(path: Path, step: str, date_cols: Iterable[str] = (), usecols: list[str] | None = None,
              text_cols: Iterable[str] = ()) -> pd.DataFrame:
    """Read one CSV, check it exists and has the expected columns, and parse dates.

    ``text_cols`` are read as strings, so long numeric codes (e.g. 17-18 digit
    SNOMED CT codes) are not turned into floats, which keep only ~16 digits.
    """
    if not path.is_file():
        raise FileNotFoundError(f"{step}: file not found: {path}")
    text_cols = list(text_cols)
    # Match on the normalised (stripped, lower-case) header, as below.
    header = {str(c).strip().lower(): c for c in pd.read_csv(path, nrows=0).columns}
    absent = [c for c in text_cols if c not in header]
    if absent:
        raise ValueError(f"{step}: {path.name} is missing text column(s) {absent}; has {list(header)}")
    dtype = {header[c]: str for c in [*text_cols, *ALWAYS_TEXT] if c in header}
    df = pd.read_csv(path, low_memory=False, dtype=dtype)
    # Snowflake exports upper-case headers; the pipeline uses lower-case names.
    df.columns = df.columns.astype(str).str.strip().str.lower()
    if usecols is not None:
        absent = [c for c in usecols if c not in df.columns]
        if absent:
            raise ValueError(f"{step}: {path.name} is missing column(s) {absent}; has {list(df.columns)}")
        df = df[list(usecols)]
    missing = [c for c in date_cols if c not in df.columns]
    if missing:
        raise ValueError(f"{step}: {path.name} is missing date column(s) {missing}; has {list(df.columns)}")
    for col in date_cols:
        df[col] = parse_datetimes(df[col], col, step)
    log.info("%s: read %d rows x %d columns from %s", step, len(df), df.shape[1], path.name)
    return df


def _lookup_path(cfg: dict, section: str, name: str) -> Path:
    """Path of ``name`` in ``cfg[section]``, with a clear error for an unknown name."""
    if name not in cfg[section]:
        raise KeyError(f"'{name}' is not in config.yaml: {section}; available: {sorted(cfg[section])}")
    return cfg[section][name]


def read_raw(cfg: dict, name: str, date_cols: Iterable[str] = (), usecols: list[str] | None = None,
             text_cols: Iterable[str] = ()) -> pd.DataFrame:
    """Read a raw SDE export named in ``config.yaml: raw_csv``.

    Parameters
    ----------
    cfg : dict
        Configuration from ``load_config``.
    name : str
        Key under ``raw_csv``, e.g. ``"demographics"``.
    date_cols : iterable of str
        Columns to parse as dates (``'none'`` and bad values become ``NaT``).
    usecols : list of str, optional
        Columns to keep. Each must exist in the file.
    text_cols : iterable of str
        Columns to read as strings (codes and identifiers). Each must exist.

    Returns
    -------
    pd.DataFrame
        The table with its original grain.
    """
    return _read_csv(_lookup_path(cfg, "raw_csv", name), f"read_raw[{name}]", date_cols, usecols, text_cols)


def read_lookup(cfg: dict, name: str) -> pd.DataFrame:
    """Read a mapping table named in ``config.yaml: lookup_csv`` (no date parsing)."""
    return _read_csv(_lookup_path(cfg, "lookup_csv", name), f"read_lookup[{name}]")


def checkpoint_path(cfg: dict, step: str) -> Path:
    """Path of the CSV checkpoint written by ``step``."""
    return cfg["paths"]["interim_dir"] / f"{step}.csv"


def write_checkpoint(df: pd.DataFrame, cfg: dict, step: str) -> Path:
    """Write ``df`` to ``data/interim/<step>.csv`` and log its size."""
    path = checkpoint_path(cfg, step)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    log.info("%s: wrote %d rows x %d columns to %s", step, len(df), df.shape[1], path)
    return path


def read_checkpoint(cfg: dict, step: str, date_cols: Iterable[str] = ()) -> pd.DataFrame:
    """Read a checkpoint, restoring dates (CSV does not store types).

    Raises
    ------
    FileNotFoundError
        If the earlier step has not been run.
    """
    path = checkpoint_path(cfg, step)
    if not path.is_file():
        raise FileNotFoundError(f"read_checkpoint: checkpoint for step '{step}' not found at {path}; run that step first.")
    return _read_csv(path, f"read_checkpoint[{step}]", date_cols)
