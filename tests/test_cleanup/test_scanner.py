"""Tests for cleanup scanner."""

from __future__ import annotations

from corp_by_os.cleanup.scanner import scan_problematic_files


def test_scan_finds_inbox_files(mywork_cleanup):
    """Scanner finds files in 00_Inbox."""
    results = scan_problematic_files(mywork_cleanup)
    inbox_files = [f for f in results if f.current_folder == "00_Inbox"]
    names = {f.name for f in inbox_files}
    assert "Sprint Planning.pptx" in names
    assert "MeetingNotes_Q4_Review.txt" in names
    assert "RFP_Response_Final.txt" in names


def test_scan_skips_triage_files(mywork_cleanup):
    """Scanner skips _triage_log.jsonl, _triage_schema.yaml, and folder_manifest.yaml."""
    results = scan_problematic_files(mywork_cleanup)
    names = {f.name for f in results}
    assert "_triage_log.jsonl" not in names
    assert "_triage_schema.yaml" not in names
    assert "folder_manifest.yaml" not in names


def test_scan_finds_url_files(mywork_cleanup):
    """Scanner finds .url files in 60_Source_Library."""
    results = scan_problematic_files(mywork_cleanup)
    url_files = [f for f in results if f.extension == ".url"]
    assert len(url_files) == 1
    assert url_files[0].name == "bookmark.url"


def test_scan_finds_log_files(mywork_cleanup):
    """Scanner finds .log files in 60_Source_Library."""
    results = scan_problematic_files(mywork_cleanup)
    log_files = [f for f in results if f.extension == ".log"]
    assert len(log_files) == 1


def test_scan_finds_rfp_loose_files(mywork_cleanup):
    """Scanner finds loose files at 50_RFP root but not subfolder contents."""
    results = scan_problematic_files(mywork_cleanup)
    rfp_files = [f for f in results if f.current_folder == "50_RFP"]
    names = {f.name for f in rfp_files}
    assert "RFP_Database_Master.xlsx" in names
    assert "RFP_Database_Planning.xlsx" in names
    # Subfolder contents should not appear
    assert "cert.pdf" not in names


def test_scan_relative_paths_forward_slashes(mywork_cleanup):
    """All relative paths use forward slashes."""
    results = scan_problematic_files(mywork_cleanup)
    for f in results:
        assert "\\" not in f.relative_path


def test_scan_total_count(mywork_cleanup):
    """Total count matches expected: 3 inbox + 2 junk + 2 rfp = 7."""
    results = scan_problematic_files(mywork_cleanup)
    assert len(results) == 7


def test_scan_empty_mywork(tmp_path):
    """Empty MyWork returns no results."""
    mywork = tmp_path / "MyWork"
    mywork.mkdir()
    results = scan_problematic_files(mywork)
    assert len(results) == 0
