"""Build the SDE upload bundle from a clean git commit.

LOCAL-ONLY tool: run it on your Mac after committing. It is excluded from the
bundle it builds and is never uploaded to the SDE.

What it does
------------
1. Refuses to run if the bundled paths have uncommitted or untracked changes,
   so the zip is guaranteed to match one commit.
2. Reads the commit hash and builds ``dist/esbl_rf_<shorthash>.zip`` from the
   *committed* files (``git archive``), never from the working tree.
3. Adds ``BUILD_INFO.json`` (commit hash, commit time, SHA-256 of every file).
   ``scripts/run_all.py`` reads it in the SDE to stamp ``outputs/run_log.txt``
   and to detect files edited after upload.
4. Refuses to bundle anything that looks like a data file.
5. Writes the short commit hash into the bundled copy of
   ``sql/pipeline/00_params.sql`` (placeholder ``{{GIT_HASH}}``), so the SQL
   tables record the commit they were built from. The committed file keeps
   the placeholder, because a commit cannot contain its own hash.

The zip is deterministic: the same commit always gives a byte-identical zip,
so the printed zip SHA-256 can be compared with ``sha256sum`` after upload.

Usage
-----
    python scripts/make_bundle.py [--out-dir dist]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Allow-list: only these tracked paths can enter the bundle (default-deny).
BUNDLE_PREFIX = "esbl_rf"
INCLUDE_PATHS = ("scripts", "src", "config.yaml", "lookups", "sql", "requirements.txt")
# Lookup tables (drug result strings, no patient data) are the only CSVs allowed in the bundle.
ALLOWED_DATA_PREFIX = "lookups/"
EXCLUDE_FILES = frozenset({"scripts/make_bundle.py"})
FORBIDDEN_SUFFIXES = frozenset(
    {".csv", ".parquet", ".feather", ".pkl", ".pickle", ".xlsx", ".xls", ".zip", ".dta", ".sav"}
)
BUILD_INFO_NAME = "BUILD_INFO.json"
HASH_FILE = "sql/pipeline/00_params.sql"
HASH_PLACEHOLDER = b"{{GIT_HASH}}"


def _git(*args: str) -> bytes:
    """Run a git command in the project root and return stdout bytes.

    Parameters
    ----------
    *args : str
        Arguments passed to ``git``.

    Returns
    -------
    bytes
        Standard output of the command.

    Raises
    ------
    RuntimeError
        If git exits with a non-zero status.
    """
    result = subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"make_bundle: `git {' '.join(args)}` failed: "
            f"{result.stderr.decode().strip()}"
        )
    return result.stdout


def check_clean_tree() -> None:
    """Raise if any bundled path has uncommitted or untracked changes.

    Raises
    ------
    RuntimeError
        Listing the offending paths. Untracked files are included because they
        would silently be missing from the bundle.
    """
    porcelain = _git("status", "--porcelain", "--", *INCLUDE_PATHS).decode()
    dirty = [
        line for line in porcelain.splitlines() if line[3:] not in EXCLUDE_FILES
    ]
    if dirty:
        raise RuntimeError(
            "make_bundle: refusing to bundle, uncommitted/untracked changes in "
            f"bundled paths ({len(dirty)}):\n  " + "\n  ".join(dirty)
            + "\nCommit (or `git add` + commit) them first."
        )


def list_bundle_files() -> list[str]:
    """List the committed files that go into the bundle.

    Returns
    -------
    list of str
        Sorted repo-relative paths at HEAD inside ``INCLUDE_PATHS``, minus
        ``EXCLUDE_FILES``.

    Raises
    ------
    RuntimeError
        If nothing matches, or if any file has a data-like suffix.
    """
    raw = _git("ls-tree", "-r", "-z", "--name-only", "HEAD", "--", *INCLUDE_PATHS)
    files = sorted(
        p for p in raw.decode().split("\0") if p and p not in EXCLUDE_FILES
    )
    if not files:
        raise RuntimeError("make_bundle: no committed files found in bundled paths.")

    forbidden = [p for p in files if Path(p).suffix.lower() in FORBIDDEN_SUFFIXES and not p.startswith(ALLOWED_DATA_PREFIX)]
    if forbidden:
        raise RuntimeError(
            "make_bundle: data-like files found in bundled paths (the bundle "
            f"must contain code and config only), {len(forbidden)}:\n  "
            + "\n  ".join(forbidden)
        )
    return files


def stamp_hash(contents: dict[str, tuple[bytes, int]], short_hash: str) -> None:
    """Replace the hash placeholder in ``HASH_FILE`` with ``short_hash`` (in place).

    Raises
    ------
    RuntimeError
        If the file is not in the bundle or the placeholder does not appear exactly once.
    """
    if HASH_FILE not in contents:
        raise RuntimeError(f"make_bundle: {HASH_FILE} is not in the bundle")
    data, mode = contents[HASH_FILE]
    n = data.count(HASH_PLACEHOLDER)
    if n != 1:
        raise RuntimeError(f"make_bundle: expected the placeholder {HASH_PLACEHOLDER.decode()} once in "
                           f"{HASH_FILE}, found {n}")
    contents[HASH_FILE] = (data.replace(HASH_PLACEHOLDER, short_hash.encode()), mode)


def build_bundle(out_dir: Path) -> Path:
    """Build the zip for the current HEAD commit.

    Parameters
    ----------
    out_dir : Path
        Directory for the zip (created if needed).

    Returns
    -------
    Path
        Path of the written zip.
    """
    check_clean_tree()
    files = list_bundle_files()

    full_hash = _git("rev-parse", "HEAD").decode().strip()
    short_hash = _git("rev-parse", "--short=7", "HEAD").decode().strip()
    commit_ts = int(_git("show", "-s", "--format=%ct", "HEAD").decode().strip())
    zip_stamp = time.gmtime(max(commit_ts, 315532800))[:6]  # zip epoch floor: 1980

    # Read file contents from the commit itself, not the working tree.
    tar_bytes = _git("archive", "--format=tar", "HEAD", "--", *files)
    contents: dict[str, tuple[bytes, int]] = {}
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        for member in tar.getmembers():
            if member.isfile():
                contents[member.name] = (tar.extractfile(member).read(), member.mode)

    missing = sorted(set(files) - set(contents))
    if missing:
        raise RuntimeError(
            f"make_bundle: {len(missing)} listed files missing from archive "
            f"(symlinks?): {missing[:5]}"
        )

    stamp_hash(contents, short_hash)   # before BUILD_INFO, so its file hashes match the zip

    build_info = {
        "bundle_format": 1,
        "git_hash": full_hash,
        "git_hash_short": short_hash,
        "commit_time_utc": datetime.fromtimestamp(commit_ts, timezone.utc).isoformat(),
        "files": {p: hashlib.sha256(contents[p][0]).hexdigest() for p in files},
    }

    folder = f"{BUNDLE_PREFIX}_{short_hash}"
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{folder}.zip"

    def _add(zf: zipfile.ZipFile, name: str, data: bytes, mode: int) -> None:
        info = zipfile.ZipInfo(f"{folder}/{name}", date_time=zip_stamp)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (0o100000 | (mode & 0o777)) << 16
        zf.writestr(info, data)

    with zipfile.ZipFile(zip_path, "w") as zf:
        for p in files:
            _add(zf, p, *contents[p])
        _add(
            zf,
            BUILD_INFO_NAME,
            (json.dumps(build_info, indent=2, sort_keys=True) + "\n").encode(),
            0o644,
        )

    zip_sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    print(f"Built {zip_path}")
    print(f"  commit    : {full_hash}")
    print(f"  files     : {len(files)} (+ {BUILD_INFO_NAME})")
    print(f"  zip sha256: {zip_sha}")
    print("  After upload, check in the SDE:  sha256sum " + zip_path.name)
    _warn_if_unpushed()
    return zip_path


def _warn_if_unpushed() -> None:
    """Warn when HEAD is on no remote branch (others could not retrieve it)."""
    try:
        remote = _git("branch", "-r", "--contains", "HEAD").decode().strip()
    except RuntimeError:
        remote = ""
    if not remote:
        print(
            "WARNING: this commit is not on any remote branch. Push it so the "
            "hash in run_log.txt can be retrieved later."
        )


def main() -> None:
    """Parse arguments and build the bundle."""
    parser = argparse.ArgumentParser(description="Build the SDE upload bundle.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("dist"),
        help="Output directory, relative to the project root (default: dist).",
    )
    args = parser.parse_args()
    out_dir = args.out_dir if args.out_dir.is_absolute() else PROJECT_ROOT / args.out_dir

    try:
        build_bundle(out_dir)
    except RuntimeError as err:
        print(err, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
