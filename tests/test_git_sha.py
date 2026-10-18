"""One git_sha helper (tech-debt F8): full sha, resolved from the repo root rather than the cwd."""

from __future__ import annotations

import re

from fantasy.model import valuation
from fantasy.ops import common


def test_single_helper_full_sha_independent_of_cwd(tmp_path, monkeypatch):
    assert valuation.git_sha is common.git_sha
    common.git_sha.cache_clear()
    here = common.git_sha()
    common.git_sha.cache_clear()
    monkeypatch.chdir(tmp_path)  # not a git checkout
    assert common.git_sha() == here
    assert here is None or re.fullmatch(r"[0-9a-f]{40}", here)
