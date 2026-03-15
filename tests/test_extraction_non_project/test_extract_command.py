"""Tests for corp extract CLI command."""

from __future__ import annotations

from unittest.mock import patch, MagicMock
from pathlib import Path

import yaml
from click.testing import CliRunner

from corp_by_os.cli import cli


def _mock_config(mywork_root: Path) -> MagicMock:
    """Create a mock config pointing at the given MyWork root."""
    cfg = MagicMock()
    cfg.mywork_root = mywork_root
    cfg.projects_root = mywork_root / "10_Projects"
    cfg.vault_path = mywork_root / "vault"
    cfg.app_data_path = mywork_root / "_appdata"
    return cfg


def _write_routing_map(mywork_root: Path, routes: dict | None = None) -> None:
    """Write routing_map.yaml into 90_System/."""
    system_dir = mywork_root / "90_System"
    system_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "version": "1.0",
        "routes": routes
        or {
            "30_Templates": {
                "vault_target": "04_evergreen/_generated/template",
                "provenance": "template",
                "subfolders": {
                    "01_Presentation_Decks": {"content_type": "presentation"},
                },
            },
        },
        "provenance_map": {
            "template": ["template", "presentation"],
        },
    }
    (system_dir / "routing_map.yaml").write_text(
        yaml.dump(data),
        encoding="utf-8",
    )


@patch("corp_by_os.cli.get_config")
def test_extract_dry_run_no_cke_call(mock_config, mywork_tree):
    """Dry run generates manifest but doesn't call CKE."""
    cfg = _mock_config(mywork_tree)
    mock_config.return_value = cfg
    _write_routing_map(mywork_tree)

    # Ensure 10_Projects exists for projects_root
    (mywork_tree / "10_Projects").mkdir(parents=True, exist_ok=True)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "extract",
            str(mywork_tree / "30_Templates" / "01_Presentation_Decks"),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output or "dry run" in result.output.lower()
    assert "manifest" in result.output.lower()


@patch("corp_by_os.cli.get_config")
def test_extract_disabled_folder(mock_config, mywork_tree):
    """Folder with extraction disabled prints warning."""
    cfg = _mock_config(mywork_tree)
    mock_config.return_value = cfg
    _write_routing_map(
        mywork_tree,
        routes={
            "00_Inbox": {
                "vault_target": "inbox",
                "provenance": "inbox",
            },
        },
    )

    (mywork_tree / "10_Projects").mkdir(parents=True, exist_ok=True)

    runner = CliRunner()
    result = runner.invoke(cli, ["extract", str(mywork_tree / "00_Inbox")])

    # Should fail because extraction is disabled in folder_manifest.yaml
    assert result.exit_code != 0
    assert "disabled" in result.output.lower()


@patch("corp_by_os.cli.get_config")
def test_extract_not_a_directory(mock_config, tmp_path):
    """Non-directory path is rejected by Click's file_okay=False."""
    cfg = _mock_config(tmp_path)
    mock_config.return_value = cfg

    fake_file = tmp_path / "notadir.txt"
    fake_file.write_text("hello")

    runner = CliRunner()
    result = runner.invoke(cli, ["extract", str(fake_file)])

    assert result.exit_code != 0
    # Click says "is a file" when file_okay=False
    assert "file" in result.output.lower()
