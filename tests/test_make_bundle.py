"""scripts/make_bundle.py: the commit hash is written into the bundled 00_params.sql."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("make_bundle", ROOT / "scripts" / "make_bundle.py")
make_bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_bundle)


def test_committed_params_keep_the_placeholder():
    assert (ROOT / make_bundle.HASH_FILE).read_bytes().count(make_bundle.HASH_PLACEHOLDER) == 1


def test_stamp_hash_replaces_placeholder():
    contents = {make_bundle.HASH_FILE: (b"x\n'{{GIT_HASH}}' AS git_hash;\n", 0o644)}
    make_bundle.stamp_hash(contents, "abc1234")
    assert contents[make_bundle.HASH_FILE] == (b"x\n'abc1234' AS git_hash;\n", 0o644)


def test_stamp_hash_refuses_without_placeholder():
    with pytest.raises(RuntimeError, match="found 0"):
        make_bundle.stamp_hash({make_bundle.HASH_FILE: (b"'82aeef7' AS git_hash;", 0o644)}, "abc1234")
