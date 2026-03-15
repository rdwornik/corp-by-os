"""
Corporate OS Agents.

Location: src/agents/__init__.py
"""

from src.agents.file_organizer import (
    ContentReader,
    DestinationFolder,
    FileAnalysis,
    FileOrganizer,
    FileType,
    NamingConvention,
    RenameProposal,
    ScanResult,
    scan_inbox,
)

__all__ = [
    "FileOrganizer",
    "FileType",
    "DestinationFolder",
    "NamingConvention",
    "ContentReader",
    "RenameProposal",
    "ScanResult",
    "FileAnalysis",
    "scan_inbox",
]
