"""Smoke tests that do not need CAM350 installed.

These run on Windows (core.py binds user32/kernel32 at import time).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys


def _run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cam350_mcp.cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=env,
    )


def test_version() -> None:
    res = _run("--version")
    assert res.returncode == 0
    assert "cam350-mcp" in res.stdout


def test_check_without_reference(tmp_path) -> None:
    env = os.environ.copy()
    env["CAM350_MCP_HOME"] = str(tmp_path / "home")
    env["CAM350_MCP_REFERENCE"] = str(tmp_path / "missing.json")
    res = _run("--check", env=env)
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert data["reference_exists"] is False
    assert "reference_hint" in data
    assert data["ok"] == data["cam350_found"]


def test_validate_skipped_without_reference(tmp_path, monkeypatch) -> None:
    from cam350_mcp import core

    monkeypatch.setattr(core, "REFERENCE", tmp_path / "missing.json")
    monkeypatch.setattr(core, "_REF_CACHE", None)

    assert core.validate("frobnicate@ nope!") == []
    info = core.reference_info()
    assert info["available"] is False
    assert "hint" in info
