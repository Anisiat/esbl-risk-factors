"""Run the manuscript pipeline end to end (entry point inside the SDE).

Runs, in order: ``clean_and_save_dfs.py`` -> ``get_table1.py`` ->
``get_missingness_univariable.py`` (all episodes, clustered by patient) ->
``get_missingness_multivariable.py`` (all episodes, clustered SEs) -> the
first-episode sensitivity analysis (``get_missingness_multivariable.py
--episodes first``). Stops at the first failing step.

Every run appends a block to ``outputs/run_log.txt`` with: git hash (from
``BUILD_INFO.json`` written by ``make_bundle.py``), timestamp, Python and
package versions, any files that differ from the bundle, and per-step status,
duration and row counts. The log holds no row-level data (child-process output
goes to the console only), because ``run_log.txt`` is exported with the results.

Usage
-----
    python scripts/run_all.py              # all steps
    python scripts/run_all.py --from table1
    python scripts/run_all.py --only clean
    python scripts/run_all.py --list
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
LOG_PATH = PROJECT_ROOT / "outputs" / "run_log.txt"
BUILD_INFO_PATH = PROJECT_ROOT / "BUILD_INFO.json"

# Packages whose versions are written to the log (SDE environment, 2026-11).
PACKAGES = (
    "pandas", "numpy", "scipy", "scikit-learn", "statsmodels", "matplotlib",
    "seaborn", "tableone", "comorbidicare", "openpyxl", "PyYAML",
)


@dataclass(frozen=True)
class Step:
    """One pipeline step.

    Attributes
    ----------
    name : str
        Name used with ``--from`` / ``--only``.
    script : str
        File name inside ``scripts/``.
    args : tuple of str
        Extra command-line arguments.
    checkpoint : Path or None
        CSV (relative to the project root) whose data-row count is logged
        after the step succeeds.
    """

    name: str
    script: str
    args: tuple[str, ...] = ()
    checkpoint: Path | None = None


# Order matters: the univariable step writes data/processed/lr_data.csv (all
# episodes) and the variables file that both multivariable runs read. Clustering
# is not allowed with --episodes first, so the sensitivity run is unclustered.
STEPS: tuple[Step, ...] = (
    Step("clean", "clean_and_save_dfs.py", checkpoint=Path("data/interim/analysis_df.csv")),
    Step("table1", "get_table1.py"),
    Step("univariable", "get_missingness_univariable.py", ("--cluster-by-patient",)),
    Step("multivariable", "get_missingness_multivariable.py", ("--cluster-by-patient",)),
    Step("sensitivity", "get_missingness_multivariable.py", ("--episodes", "first")),
)


def log(message: str = "") -> None:
    """Print a line and append it to the run log."""
    print(message)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def describe_code_version() -> tuple[str, list[str]]:
    """Identify the code version and detect post-upload edits.

    Returns
    -------
    tuple of (str, list of str)
        A description of the code version, and a list of problems (files that
        are modified or missing relative to the bundle manifest).
    """
    if BUILD_INFO_PATH.exists():
        info = json.loads(BUILD_INFO_PATH.read_text(encoding="utf-8"))
        problems = []
        for rel_path, expected in info["files"].items():
            path = PROJECT_ROOT / rel_path
            if not path.is_file():
                problems.append(f"MISSING  {rel_path}")
            elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                problems.append(f"MODIFIED {rel_path}")
        return f"{info['git_hash']} (bundle, commit {info['commit_time_utc']})", problems

    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=PROJECT_ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return f"{head}{' + UNCOMMITTED CHANGES' if dirty else ''} (git checkout, not a bundle)", []
    except (OSError, subprocess.CalledProcessError):
        return "unknown (no BUILD_INFO.json and no git repository)", []


def package_versions() -> list[str]:
    """Return ``name==version`` lines for the logged packages."""
    lines = []
    for name in PACKAGES:
        try:
            lines.append(f"{name}=={metadata.version(name)}")
        except metadata.PackageNotFoundError:
            lines.append(f"{name}: NOT INSTALLED")
    return lines


def count_rows(path: Path) -> int:
    """Count data rows (excluding the header) in a CSV, quote-aware."""
    with path.open(newline="", encoding="utf-8") as fh:
        return max(sum(1 for _ in csv.reader(fh)) - 1, 0)


def select_steps(start: str | None, only: str | None) -> list[Step]:
    """Choose the steps to run from ``--from`` / ``--only``."""
    names = [s.name for s in STEPS]
    if only:
        return [s for s in STEPS if s.name == only]
    if start:
        return list(STEPS[names.index(start):])
    return list(STEPS)


def run_step(step: Step) -> bool:
    """Run one step as a subprocess and log the outcome.

    The working directory is ``scripts/``, as in the manuscript run. It does not
    affect data paths: ``load_config`` turns every relative path in
    ``config.yaml`` into an absolute path anchored at the config file.

    Returns
    -------
    bool
        True if the step exited with status 0.
    """
    script = SCRIPTS_DIR / step.script
    if not script.is_file():
        log(f"[{step.name}] FAILED: {script} not found")
        return False

    log(f"[{step.name}] running {step.script} {' '.join(step.args)}".rstrip())
    start = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(script), *step.args], cwd=SCRIPTS_DIR, check=False
    )
    elapsed = time.monotonic() - start

    if result.returncode != 0:
        log(f"[{step.name}] FAILED with exit code {result.returncode} after {elapsed:.0f}s")
        return False

    log(f"[{step.name}] ok ({elapsed:.0f}s)")
    if step.checkpoint is not None:
        path = PROJECT_ROOT / step.checkpoint
        if not path.is_file():
            log(f"[{step.name}] FAILED: expected output {step.checkpoint} was not written")
            return False
        log(f"[{step.name}] {step.checkpoint}: {count_rows(path)} rows")
    return True


def main() -> None:
    """Parse arguments, write the log header, and run the selected steps."""
    names = [s.name for s in STEPS]
    parser = argparse.ArgumentParser(description="Run the ESBL risk-factor pipeline.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--from", dest="start", choices=names, help="Run from this step onwards.")
    group.add_argument("--only", choices=names, help="Run only this step.")
    parser.add_argument("--list", action="store_true", help="List steps and exit.")
    args = parser.parse_args()

    if args.list:
        for step in STEPS:
            print(f"{step.name:<14}{step.script} {' '.join(step.args)}".rstrip())
        return

    steps = select_steps(args.start, args.only)
    version, problems = describe_code_version()

    log("=" * 70)
    log(f"Run started : {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    log(f"Code version: {version}")
    log(f"Python      : {platform.python_version()} ({platform.platform()})")
    log("Packages    : " + ", ".join(package_versions()))
    if problems:
        log(f"WARNING: {len(problems)} file(s) differ from the uploaded bundle:")
        for line in problems:
            log(f"  {line}")
    log("Steps       : " + ", ".join(s.name for s in steps))

    for step in steps:
        if not run_step(step):
            log("Pipeline stopped at the failing step above.")
            sys.exit(1)

    log("Pipeline completed successfully.")


if __name__ == "__main__":
    main()
