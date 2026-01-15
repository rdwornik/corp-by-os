"""
Corporate OS Agents.

Location: src/agents/__init__.py
"""

from src.agents.file_organizer import (
    FileOrganizer,
    FileType,
    NamingConvention,
    RenameProposal,
    ScanResult,
    scan_inbox,
    scan_recordings,
)

__all__ = [
    "FileOrganizer",
    "FileType",
    "NamingConvention",
    "RenameProposal",
    "ScanResult",
    "scan_inbox",
    "scan_recordings",
]
