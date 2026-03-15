"""Tests for overnight safety gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from corp_by_os.overnight.safety import is_safe_for_upload, filter_safe_files


class TestBlockedExtension:
    @pytest.mark.parametrize("ext", [".env", ".pem", ".key", ".kdbx", ".pfx"])
    def test_blocked_extension(self, tmp_path: Path, ext: str) -> None:
        f = tmp_path / f"secret{ext}"
        f.write_text("content", encoding="utf-8")
        ok, reason = is_safe_for_upload(f)
        assert not ok
        assert "blocked extension" in reason


class TestBlockedPattern:
    @pytest.mark.parametrize(
        "rel_path",
        [
            "project/secrets/api.yaml",
            "repo/.git/config",
            "app/.venv/lib/something.py",
            "data/__pycache__/mod.pyc",
        ],
    )
    def test_blocked_pattern(self, tmp_path: Path, rel_path: str) -> None:
        f = tmp_path / rel_path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("data", encoding="utf-8")
        ok, reason = is_safe_for_upload(f)
        assert not ok
        assert "blocked path pattern" in reason


class TestBlockedContent:
    def test_private_key(self, tmp_path: Path) -> None:
        f = tmp_path / "cert.txt"
        f.write_text("-----BEGIN PRIVATE KEY-----\nMIIEv...", encoding="utf-8")
        ok, reason = is_safe_for_upload(f)
        assert not ok
        assert "sensitive content" in reason

    def test_aws_key(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("aws_key: AKIAIOSFODNN7EXAMPLE", encoding="utf-8")
        ok, reason = is_safe_for_upload(f)
        assert not ok
        assert "sensitive content" in reason

    def test_password_in_config(self, tmp_path: Path) -> None:
        f = tmp_path / "settings.ini"
        f.write_text("password = hunter2\n", encoding="utf-8")
        ok, reason = is_safe_for_upload(f)
        assert not ok
        assert "sensitive content" in reason

    def test_github_token(self, tmp_path: Path) -> None:
        f = tmp_path / "notes.md"
        f.write_text("token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh1234\n", encoding="utf-8")
        ok, reason = is_safe_for_upload(f)
        assert not ok
        assert "sensitive content" in reason


class TestSafeFile:
    def test_safe_pptx(self, tmp_path: Path) -> None:
        f = tmp_path / "presentation.pptx"
        f.write_bytes(b"PK\x03\x04fake-zip-content")
        ok, reason = is_safe_for_upload(f)
        assert ok
        assert reason == ""

    def test_safe_pdf(self, tmp_path: Path) -> None:
        f = tmp_path / "document.pdf"
        f.write_bytes(b"%PDF-1.4 fake content")
        ok, reason = is_safe_for_upload(f)
        assert ok

    def test_safe_docx(self, tmp_path: Path) -> None:
        f = tmp_path / "report.docx"
        f.write_bytes(b"PK\x03\x04fake-docx")
        ok, reason = is_safe_for_upload(f)
        assert ok

    def test_safe_text_without_secrets(self, tmp_path: Path) -> None:
        f = tmp_path / "notes.txt"
        f.write_text("Just regular meeting notes about Q2 planning.", encoding="utf-8")
        ok, reason = is_safe_for_upload(f)
        assert ok


class TestFilterSafeFiles:
    def test_filter_mixed(self, tmp_path: Path) -> None:
        safe_file = tmp_path / "doc.pdf"
        safe_file.write_bytes(b"%PDF")
        blocked_file = tmp_path / "secret.env"
        blocked_file.write_text("KEY=val", encoding="utf-8")

        safe, blocked = filter_safe_files([safe_file, blocked_file])
        assert len(safe) == 1
        assert len(blocked) == 1
        assert blocked[0][0] == blocked_file
        assert "blocked extension" in blocked[0][1]
