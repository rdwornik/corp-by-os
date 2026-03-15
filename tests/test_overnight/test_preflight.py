"""Tests for overnight pre-flight checks."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from corp_by_os.overnight.preflight import run_preflight


@pytest.fixture()
def valid_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Set up a valid environment for pre-flight checks."""
    mywork = tmp_path / "mywork"
    vault = tmp_path / "vault"
    appdata = tmp_path / "appdata"

    mywork.mkdir()
    vault.mkdir()
    appdata.mkdir()

    # Create valid routing_map.yaml
    routing_dir = mywork / "90_System"
    routing_dir.mkdir(parents=True)
    routing_map = routing_dir / "routing_map.yaml"
    routing_map.write_text(
        yaml.dump({"folders": {"30_Templates": {"vault_target": "05_templates"}}}),
        encoding="utf-8",
    )

    # Set API key
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-12345")

    return {"mywork": mywork, "vault": vault, "appdata": appdata}


class TestPreflightAllOk:
    def test_preflight_all_ok(self, valid_env: dict) -> None:
        errors = run_preflight(
            valid_env["mywork"],
            valid_env["vault"],
            valid_env["appdata"],
        )
        assert errors == []


class TestPreflightApiKey:
    def test_preflight_missing_api_key(
        self,
        valid_env: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        errors = run_preflight(
            valid_env["mywork"],
            valid_env["vault"],
            valid_env["appdata"],
        )
        assert any("GEMINI_API_KEY" in e for e in errors)


class TestPreflightPaths:
    def test_missing_vault(self, valid_env: dict) -> None:
        import shutil

        shutil.rmtree(valid_env["vault"])
        errors = run_preflight(
            valid_env["mywork"],
            valid_env["vault"],
            valid_env["appdata"],
        )
        assert any("Vault path does not exist" in e for e in errors)

    def test_missing_mywork(self, valid_env: dict) -> None:
        import shutil

        shutil.rmtree(valid_env["mywork"])
        errors = run_preflight(
            valid_env["mywork"],
            valid_env["vault"],
            valid_env["appdata"],
        )
        assert any("MyWork root does not exist" in e for e in errors)


class TestPreflightRoutingMap:
    def test_missing_routing_map(self, valid_env: dict) -> None:
        (valid_env["mywork"] / "90_System" / "routing_map.yaml").unlink()
        errors = run_preflight(
            valid_env["mywork"],
            valid_env["vault"],
            valid_env["appdata"],
        )
        assert any("routing_map.yaml not found" in e for e in errors)

    def test_invalid_routing_map(self, valid_env: dict) -> None:
        rm = valid_env["mywork"] / "90_System" / "routing_map.yaml"
        rm.write_text("- not\n- a\n- dict\n", encoding="utf-8")
        errors = run_preflight(
            valid_env["mywork"],
            valid_env["vault"],
            valid_env["appdata"],
        )
        assert any("not a valid YAML dict" in e for e in errors)


class TestPreflightDiskSpace:
    def test_low_disk_space(self, valid_env: dict) -> None:
        # Mock shutil.disk_usage to report low space
        fake_usage = type("Usage", (), {"free": 1024**3})()  # 1GB
        with patch("corp_by_os.overnight.preflight.shutil.disk_usage", return_value=fake_usage):
            errors = run_preflight(
                valid_env["mywork"],
                valid_env["vault"],
                valid_env["appdata"],
            )
        assert any("Low disk space" in e for e in errors)


class TestPreflightLockFiles:
    def test_stale_lock_file(self, valid_env: dict) -> None:
        lock_dir = valid_env["appdata"] / "state"
        lock_dir.mkdir(parents=True)
        (lock_dir / "overnight.lock").write_text("pid=1234", encoding="utf-8")

        errors = run_preflight(
            valid_env["mywork"],
            valid_env["vault"],
            valid_env["appdata"],
        )
        assert any("Stale lock file" in e for e in errors)
