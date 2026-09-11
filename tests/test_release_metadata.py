from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def test_project_versions_are_consistent() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    uv_lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))

    project_entries = [
        entry for entry in uv_lock["package"] if entry["name"] == pyproject["project"]["name"]
    ]
    assert len(project_entries) == 1

    versions = {
        "pyproject.toml": pyproject["project"]["version"],
        "uv.lock": project_entries[0]["version"],
        "package.json": package["version"],
        "package-lock.json": package_lock["version"],
        'package-lock.json packages[""]': package_lock["packages"][""]["version"],
    }

    assert len(set(versions.values())) == 1, versions
    assert SEMVER.fullmatch(next(iter(versions.values()))) is not None


def test_current_version_has_release_artifacts() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    release_path = ROOT / "docs" / f"release-v{version}.md"

    assert release_path.is_file()
    assert f"# Release v{version}" in release_path.read_text(encoding="utf-8")
    assert f"## [{version}]" in changelog
    assert f"v{version}" in readme
