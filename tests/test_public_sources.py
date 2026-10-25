import hashlib
import json
import shutil
from pathlib import Path

import pytest
from pipeline.profiles import source_path


def test_clean_checkout_sources_and_integrity(tmp_path):
    source = Path(__file__).resolve().parents[1] / "docs/product-sources"
    target = tmp_path / "docs/product-sources"
    shutil.copytree(source, target)
    for row in json.loads((target / "manifest.json").read_text(encoding="utf-8"))["files"]:
        path = source_path(".work/handbook/" + row["file"] + "#section", tmp_path)
        assert path == target / row["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
    (target / "dongting-brief.md").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        source_path(".work/handbook/dongting-brief.md", tmp_path)


def test_explicit_work_source_is_preserved(tmp_path):
    work = tmp_path / "custom-work"
    path = work / "handbook/dongting-brief.md"
    path.parent.mkdir(parents=True)
    path.write_text("explicit original", encoding="utf-8")
    assert source_path(".work/handbook/dongting-brief.md", tmp_path, work) == path
    assert source_path("unknown.md", tmp_path) == tmp_path / "unknown.md"
