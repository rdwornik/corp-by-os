"""Tests for routing.py — route resolution from routing_map.yaml."""

from __future__ import annotations

import pytest

from corp_by_os.extraction.non_project.routing import (
    RouteInfo,
    RoutingError,
    resolve_route,
)


def test_resolve_route_templates(routing_map, mywork_tree):
    """30_Templates/ resolves to template provenance."""
    folder = mywork_tree / "30_Templates"
    info = resolve_route(folder, routing_map, mywork_root=mywork_tree)
    assert info.provenance_scope == "template"
    assert info.vault_target == "04_evergreen/_generated/template"
    assert info.content_origin == "mywork"
    assert info.routing_confidence == 1.0


def test_resolve_route_subfolder(routing_map, mywork_tree):
    """30_Templates/01_Presentation_Decks/ gets subfolder-specific source_category."""
    folder = mywork_tree / "30_Templates" / "01_Presentation_Decks"
    info = resolve_route(folder, routing_map, mywork_root=mywork_tree)
    assert info.source_category == "presentation"
    # presentation maps to template scope via provenance_map
    assert info.provenance_scope == "template"


def test_resolve_route_source_library(routing_map, mywork_tree):
    """60_Source_Library/ resolves to evergreen provenance."""
    folder = mywork_tree / "60_Source_Library"
    info = resolve_route(folder, routing_map, mywork_root=mywork_tree)
    assert info.provenance_scope == "evergreen"
    assert info.source_category == "evergreen"


def test_resolve_route_source_library_subfolder(routing_map, mywork_tree):
    """60_Source_Library/01_Product_Docs/ gets product_doc source_category."""
    folder = mywork_tree / "60_Source_Library" / "01_Product_Docs"
    info = resolve_route(folder, routing_map, mywork_root=mywork_tree)
    assert info.source_category == "product_doc"
    assert info.provenance_scope == "evergreen"


def test_resolve_route_unknown_folder(routing_map, mywork_tree):
    """Unknown folder raises RoutingError."""
    folder = mywork_tree / "99_Unknown"
    folder.mkdir(parents=True)
    with pytest.raises(RoutingError, match="No route defined"):
        resolve_route(folder, routing_map, mywork_root=mywork_tree)


def test_resolve_route_outside_mywork(routing_map, tmp_path):
    """Folder outside mywork_root raises RoutingError."""
    mywork = tmp_path / "MyWork"
    mywork.mkdir()
    outside = tmp_path / "Other"
    outside.mkdir()
    with pytest.raises(RoutingError, match="not under MyWork root"):
        resolve_route(outside, routing_map, mywork_root=mywork)
