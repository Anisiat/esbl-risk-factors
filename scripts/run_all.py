"""Run the pipeline steps in order (entry point inside the SDE).

Usage
-----
    python scripts/run_all.py                  # all steps
    python scripts/run_all.py --from episodes  # this step and later ones
    python scripts/run_all.py --only label     # one step
    python scripts/run_all.py --list

Every run appends a block to ``outputs/run_log.txt``: git hash, timestamp, Python and
package versions, then the log of each step (row counts at every step). The log
holds counts, not row-level data, but its counts are NOT suppressed: check it before it leaves the SDE.
"""
from __future__ import annotations

import argparse
import json
import logging
import platform
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from esbl_rf.cohort_derivation import reset_derivation  # noqa: E402
from esbl_rf.config import load_config  # noqa: E402
from esbl_rf.steps import STEPS  # noqa: E402

PACKAGES = ["pandas", "numpy", "pyyaml", "statsmodels", "comorbidicare", "matplotlib", "openpyxl"]


def git_hash() -> str:
    """Commit of the code being run: BUILD_INFO.json (written by make_bundle.py in the SDE), else git."""
    info = ROOT / "BUILD_INFO.json"
    if info.is_file():
        return str(json.loads(info.read_text()).get("git_hash", "unknown"))
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def versions() -> str:
    out = []
    for name in PACKAGES:
        try:
            out.append(f"{name}={metadata.version(name)}")
        except metadata.PackageNotFoundError:
            out.append(f"{name}=not installed")
    return ", ".join(out)


def selected_steps(args: argparse.Namespace) -> list[str]:
    names = list(STEPS)
    if args.only:
        return [args.only]
    if args.from_step:
        return names[names.index(args.from_step):]
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--only", choices=list(STEPS))
    group.add_argument("--from", dest="from_step", choices=list(STEPS))
    group.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        print("\n".join(STEPS))
        return 0

    cfg = load_config(args.config)
    log_path = ROOT / "outputs" / "run_log.txt"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler(), logging.FileHandler(log_path, mode="a", encoding="utf-8")]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers, force=True)
    log = logging.getLogger("run_all")
    log.info("=" * 70)
    log.info("run started %s | git %s | python %s | %s", datetime.now(timezone.utc).isoformat(timespec="seconds"),
             git_hash(), platform.python_version(), versions())

    steps = selected_steps(args)
    if "cohort" in steps:
        reset_derivation(cfg)   # the log is rebuilt from the cohort step (also on a full run)
    for name in steps:
        log.info("--- step: %s", name)
        try:
            STEPS[name](cfg)
        except Exception:
            log.exception("step '%s' failed; later steps not run", name)
            return 1
    log.info("run finished: %s", ", ".join(steps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
