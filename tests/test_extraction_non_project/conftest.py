"""Shared fixtures for non-project extraction tests."""

from __future__ import annotations

import pytest


@pytest.fixture()
def routing_map() -> dict:
    """Minimal routing_map.yaml as dict."""
    return {
        "version": "1.0",
        "routes": {
            "30_Templates": {
                "description": "Templates",
                "vault_target": "04_evergreen/_generated/template",
                "provenance": "template",
                "subfolders": {
                    "01_Presentation_Decks": {"content_type": "presentation"},
                    "02_Demo_Scripts": {"content_type": "demo"},
                    "03_Discovery_Tools": {"content_type": "discovery"},
                },
            },
            "60_Source_Library": {
                "description": "Source library",
                "vault_target": "04_evergreen/_generated/evergreen",
                "provenance": "evergreen",
                "subfolders": {
                    "01_Product_Docs": {"content_type": "product_doc"},
                    "02_Training_Enablement": {"content_type": "training"},
                    "03_Competitive": {"content_type": "competitive"},
                },
            },
            "00_Inbox": {
                "description": "Inbox",
                "vault_target": None,
                "provenance": None,
                "triage_required": True,
            },
        },
        "provenance_map": {
            "template": ["template", "demo", "presentation"],
            "evergreen": ["product_doc", "training", "competitive"],
            "project": ["meeting", "rfp_response"],
        },
    }


@pytest.fixture()
def mywork_tree(tmp_path):
    """Create a minimal MyWork-like directory structure with test files."""
    mywork = tmp_path / "MyWork"

    # 30_Templates with subfolders
    templates = mywork / "30_Templates"
    decks = templates / "01_Presentation_Decks"
    demos = templates / "02_Demo_Scripts"
    decks.mkdir(parents=True)
    demos.mkdir(parents=True)
    (decks / "overview.pptx").write_bytes(b"fake-pptx")
    (decks / "architecture.pptx").write_bytes(b"fake-pptx-2")
    (demos / "api_demo.json").write_bytes(b'{"demo": true}')
    (demos / "data.csv").write_text("a,b,c\n1,2,3\n", encoding="utf-8")

    # _knowledge dir (should be skipped by scanner)
    knowledge = templates / "_knowledge"
    knowledge.mkdir()
    (knowledge / "extract.json").write_text("{}", encoding="utf-8")

    # Hidden file (should be skipped)
    (decks / ".hidden").write_text("hidden", encoding="utf-8")

    # folder_manifest.yaml for templates
    (templates / "folder_manifest.yaml").write_text(
        "purpose: Templates\n"
        "extraction:\n"
        "  enabled: true\n"
        "  scope: template\n"
        "  extract_on_change: true\n"
        "  settle_minutes: 5\n"
        "allow_extensions: [.pptx, .xlsx, .pdf, .json, .csv]\n"
        "privacy: internal\n"
        "subfolders:\n"
        "  02_Demo_Scripts:\n"
        "    credential_scrubbing: true\n",
        encoding="utf-8",
    )

    # Subfolder manifest for demos
    (demos / "folder_manifest.yaml").write_text(
        "purpose: Demo scripts\n"
        "extraction:\n"
        "  enabled: true\n"
        "  scope: template\n"
        "  credential_scrubbing: true\n"
        "allow_extensions: [.pptx, .py, .json, .csv, .xlsx, .md]\n"
        "privacy: internal\n",
        encoding="utf-8",
    )

    # 60_Source_Library
    source_lib = mywork / "60_Source_Library"
    prod_docs = source_lib / "01_Product_Docs"
    prod_docs.mkdir(parents=True)
    (prod_docs / "platform_spec.pdf").write_bytes(b"fake-pdf")

    # 00_Inbox (extraction disabled)
    inbox = mywork / "00_Inbox"
    inbox.mkdir(parents=True)
    (inbox / "folder_manifest.yaml").write_text(
        "purpose: Inbox\nextraction:\n  enabled: false\n",
        encoding="utf-8",
    )

    return mywork
