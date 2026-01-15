"""
FileOrganizer Agent - Scans folders and proposes file renames.

Location: src/agents/file_organizer.py

Naming convention: [TYPE]_[Description]_[YYYY-MM-DD]_[vNN].ext

Types: MeetingNotes, Recording, Transcript, Presentation,
       Document, RFP, Email, Screenshot, Diagram
"""

import re
import shutil
import logging
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Literal
from enum import Enum

from config.settings import get_settings

logger = logging.getLogger(__name__)


class FileType(Enum):
    """Supported file types for naming convention."""
    MEETING_NOTES = "MeetingNotes"
    RECORDING = "Recording"
    TRANSCRIPT = "Transcript"
    PRESENTATION = "Presentation"
    DOCUMENT = "Document"
    RFP = "RFP"
    EMAIL = "Email"
    SCREENSHOT = "Screenshot"
    DIAGRAM = "Diagram"
    UNKNOWN = "Unknown"


@dataclass
class RenameProposal:
    """Proposed rename for a file."""
    original_path: Path
    new_name: str
    new_path: Path
    file_type: FileType
    reason: str
    confidence: float  # 0.0 - 1.0
    needs_review: bool = False

    @property
    def original_name(self) -> str:
        return self.original_path.name


@dataclass
class ScanResult:
    """Result of folder scan."""
    folder: Path
    total_files: int
    already_compliant: int
    proposals: list[RenameProposal] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def needs_rename(self) -> int:
        return len(self.proposals)

    @property
    def compliance_rate(self) -> float:
        if self.total_files == 0:
            return 1.0
        return self.already_compliant / self.total_files


class NamingConvention:
    """
    File naming convention logic.

    Format: [TYPE]_[Description]_[YYYY-MM-DD]_[vNN].ext
    """

    # Regex for valid name format
    VALID_PATTERN = re.compile(
        r'^([A-Z][a-zA-Z]+)'           # TYPE (PascalCase)
        r'_([A-Z][a-zA-Z0-9_]+)'       # Description (PascalCase with _, allows digits)
        r'_(\d{4}-\d{2}-\d{2})'        # Date (YYYY-MM-DD)
        r'(?:_v(\d{2}))?'              # Version (optional)
        r'\.([a-zA-Z0-9]+)$'           # Extension
    )

    # Extension to type mapping
    EXTENSION_MAP = {
        # Recordings
        '.mkv': FileType.RECORDING,
        '.mp4': FileType.RECORDING,
        '.webm': FileType.RECORDING,
        '.m4a': FileType.RECORDING,
        '.mp3': FileType.RECORDING,
        '.wav': FileType.RECORDING,
        # Presentations
        '.pptx': FileType.PRESENTATION,
        '.ppt': FileType.PRESENTATION,
        '.key': FileType.PRESENTATION,
        # Documents
        '.docx': FileType.DOCUMENT,
        '.doc': FileType.DOCUMENT,
        '.pdf': FileType.DOCUMENT,
        '.txt': FileType.DOCUMENT,
        # Transcripts / Notes (markdown)
        '.md': FileType.MEETING_NOTES,
        # Diagrams
        '.drawio': FileType.DIAGRAM,
        '.vsdx': FileType.DIAGRAM,
        # Screenshots
        '.png': FileType.SCREENSHOT,
        '.jpg': FileType.SCREENSHOT,
        '.jpeg': FileType.SCREENSHOT,
        # Email
        '.eml': FileType.EMAIL,
        '.msg': FileType.EMAIL,
        # Excel (often RFP related)
        '.xlsx': FileType.DOCUMENT,
        '.xls': FileType.DOCUMENT,
    }

    # Keywords to detect file types
    TYPE_KEYWORDS = {
        FileType.MEETING_NOTES: ['meeting', 'notes', 'minutes', 'spotkanie', 'notatki'],
        FileType.RECORDING: ['recording', 'rec', 'call', 'nagranie', 'video'],
        FileType.TRANSCRIPT: ['transcript', 'transkrypcja', 'transkrypt'],
        FileType.PRESENTATION: ['presentation', 'prez', 'deck', 'slides', 'prezentacja'],
        FileType.RFP: ['rfp', 'rfq', 'rfi', 'tender', 'przetarg', 'zapytanie'],
        FileType.EMAIL: ['email', 'mail', 'message', 'wiadomość'],
        FileType.SCREENSHOT: ['screenshot', 'screen', 'capture', 'zrzut'],
        FileType.DIAGRAM: ['diagram', 'architecture', 'flow', 'schemat', 'architektura'],
    }

    @classmethod
    def is_compliant(cls, filename: str) -> bool:
        """Check if filename follows naming convention."""
        return cls.VALID_PATTERN.match(filename) is not None

    @classmethod
    def parse(cls, filename: str) -> Optional[dict]:
        """Parse compliant filename into components."""
        match = cls.VALID_PATTERN.match(filename)
        if not match:
            return None
        return {
            'type': match.group(1),
            'description': match.group(2),
            'date': match.group(3),
            'version': match.group(4),
            'extension': match.group(5),
        }

    @classmethod
    def detect_type(cls, path: Path) -> FileType:
        """Detect file type from extension and name."""
        ext = path.suffix.lower()
        name_lower = path.stem.lower()

        # Check keywords first
        for file_type, keywords in cls.TYPE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in name_lower:
                    return file_type

        # Fallback to extension
        return cls.EXTENSION_MAP.get(ext, FileType.UNKNOWN)

    @classmethod
    def extract_date(cls, path: Path) -> Optional[str]:
        """Extract date from filename or file metadata."""
        name = path.stem

        # Try common date patterns in filename
        patterns = [
            r'(\d{4}-\d{2}-\d{2})',           # ISO: 2025-01-15
            r'(\d{4}_\d{2}_\d{2})',           # Underscore: 2025_01_15
            r'(\d{2}-\d{2}-\d{4})',           # US: 01-15-2025
            r'(\d{2}\.\d{2}\.\d{4})',         # EU: 15.01.2025
            r'(\d{8})',                        # Compact: 20250115
        ]

        for pattern in patterns:
            match = re.search(pattern, name)
            if match:
                date_str = match.group(1)
                return cls._normalize_date(date_str)

        # Fallback to file modification time
        try:
            mtime = path.stat().st_mtime
            return datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
        except Exception:
            return datetime.now().strftime('%Y-%m-%d')

    @classmethod
    def _normalize_date(cls, date_str: str) -> str:
        """Normalize various date formats to ISO YYYY-MM-DD."""
        date_str = date_str.replace('_', '-').replace('.', '-')

        # Already ISO
        if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
            return date_str

        # Compact: 20250115
        if re.match(r'^\d{8}$', date_str):
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

        # US/EU format: need to parse
        parts = date_str.split('-')
        if len(parts) == 3:
            if len(parts[2]) == 4:  # Year at end
                if int(parts[0]) > 12:  # Day first (EU)
                    return f"{parts[2]}-{parts[1]}-{parts[0]}"
                else:  # Month first (US)
                    return f"{parts[2]}-{parts[0]}-{parts[1]}"

        return date_str

    @classmethod
    def clean_description(cls, text: str) -> str:
        """Clean and format description for naming convention."""
        # Remove common prefixes/suffixes
        text = re.sub(r'^(re|fwd|fw):\s*', '', text, flags=re.I)
        text = re.sub(r'\s*\(copy\)|\s*-\s*copy', '', text, flags=re.I)

        # Remove date patterns (will be added separately)
        text = re.sub(r'\d{4}[-_]\d{2}[-_]\d{2}', '', text)
        text = re.sub(r'\d{2}[-_.]\d{2}[-_.]\d{4}', '', text)
        text = re.sub(r'\d{8}', '', text)

        # Remove version patterns (will be added separately)
        text = re.sub(r'[_\s]?v\d+', '', text, flags=re.I)
        text = re.sub(r'[_\s]?version\s*\d+', '', text, flags=re.I)

        # Replace separators with spaces
        text = re.sub(r'[-_\s]+', ' ', text)

        # Clean up
        text = text.strip()

        # Convert to PascalCase with underscores
        words = text.split()
        words = [w.capitalize() for w in words if w]

        if not words:
            return "Untitled"

        return '_'.join(words)

    @classmethod
    def extract_version(cls, filename: str) -> Optional[str]:
        """Extract version number from filename."""
        match = re.search(r'[_\s]v(\d{1,2})', filename, re.I)
        if match:
            return f"v{int(match.group(1)):02d}"
        return None

    @classmethod
    def build_name(
        cls,
        file_type: FileType,
        description: str,
        date: str,
        extension: str,
        version: Optional[str] = None
    ) -> str:
        """Build compliant filename."""
        parts = [
            file_type.value,
            cls.clean_description(description),
            date,
        ]

        if version:
            parts.append(version)

        name = '_'.join(parts)

        # Ensure extension has dot
        if not extension.startswith('.'):
            extension = f'.{extension}'

        return f"{name}{extension}"


class FileOrganizer:
    """
    Agent that scans folders and proposes file renames.

    Usage:
        organizer = FileOrganizer()
        result = organizer.scan(folder_path)
        organizer.preview(result)
        organizer.apply(result, dry_run=True)  # Preview only
        organizer.apply(result, dry_run=False)  # Actually rename
    """

    def __init__(self, use_llm: bool = False):
        """
        Initialize organizer.

        Args:
            use_llm: If True, use LLM for smarter description extraction.
                     Requires Ollama to be running.
        """
        self.settings = get_settings()
        self.use_llm = use_llm
        self._llm_router = None

        if use_llm:
            try:
                from src.core.llm.router import get_router
                self._llm_router = get_router()
            except Exception as e:
                logger.warning(f"LLM not available: {e}")
                self.use_llm = False

    def scan(
        self,
        folder: Path,
        recursive: bool = False,
        extensions: Optional[list[str]] = None
    ) -> ScanResult:
        """
        Scan folder and generate rename proposals.

        Args:
            folder: Folder to scan
            recursive: Scan subfolders
            extensions: Filter by extensions (e.g., ['.mkv', '.pptx'])
        """
        folder = Path(folder)
        if not folder.exists():
            return ScanResult(
                folder=folder,
                total_files=0,
                already_compliant=0,
                errors=[f"Folder does not exist: {folder}"]
            )

        # Collect files
        if recursive:
            files = list(folder.rglob('*'))
        else:
            files = list(folder.glob('*'))

        # Filter to files only
        files = [f for f in files if f.is_file()]

        # Filter by extension if specified
        if extensions:
            extensions = [e.lower() if e.startswith('.') else f'.{e.lower()}' for e in extensions]
            files = [f for f in files if f.suffix.lower() in extensions]

        # Skip system files
        files = [f for f in files if not f.name.startswith('.')]
        files = [f for f in files if not f.name.startswith('~')]

        result = ScanResult(
            folder=folder,
            total_files=len(files),
            already_compliant=0
        )

        for file_path in files:
            try:
                if NamingConvention.is_compliant(file_path.name):
                    result.already_compliant += 1
                else:
                    proposal = self._create_proposal(file_path)
                    if proposal:
                        result.proposals.append(proposal)
            except Exception as e:
                result.errors.append(f"{file_path.name}: {e}")

        return result

    def _create_proposal(self, path: Path) -> Optional[RenameProposal]:
        """Create rename proposal for a file."""
        # Detect components
        file_type = NamingConvention.detect_type(path)
        date = NamingConvention.extract_date(path)
        version = NamingConvention.extract_version(path.name)

        # Get description
        if self.use_llm and self._llm_router:
            description = self._llm_extract_description(path)
            confidence = 0.8
        else:
            description = self._rule_extract_description(path)
            confidence = 0.6

        # Build new name
        new_name = NamingConvention.build_name(
            file_type=file_type,
            description=description,
            date=date,
            extension=path.suffix,
            version=version
        )

        # Handle duplicates
        new_path = path.parent / new_name
        if new_path.exists() and new_path != path:
            # Add version suffix
            counter = 1
            while new_path.exists():
                ver = f"v{counter:02d}"
                new_name = NamingConvention.build_name(
                    file_type=file_type,
                    description=description,
                    date=date,
                    extension=path.suffix,
                    version=ver
                )
                new_path = path.parent / new_name
                counter += 1

        # Skip if no change needed
        if new_name == path.name:
            return None

        return RenameProposal(
            original_path=path,
            new_name=new_name,
            new_path=new_path,
            file_type=file_type,
            reason=self._generate_reason(path, file_type),
            confidence=confidence,
            needs_review=file_type == FileType.UNKNOWN or confidence < 0.7
        )

    def _rule_extract_description(self, path: Path) -> str:
        """Extract description using rules."""
        stem = path.stem

        # Remove common type indicators
        for file_type, keywords in NamingConvention.TYPE_KEYWORDS.items():
            for keyword in keywords:
                stem = re.sub(rf'\b{keyword}\b', '', stem, flags=re.I)

        return NamingConvention.clean_description(stem)

    def _llm_extract_description(self, path: Path) -> str:
        """Use LLM to extract meaningful description."""
        prompt = f"""Extract a short description (2-4 words) for this filename.
The description should be in PascalCase with underscores.

Filename: {path.name}

Rules:
- Remove dates, versions, file types
- Keep company names, topics, meeting types
- Use English, capitalize each word
- Separate words with underscores

Examples:
- "zoom_2025-01-15_meeting.mkv" -> "Zoom_Meeting"
- "Honda PALOMA discovery call.pptx" -> "Honda_Paloma_Discovery"
- "Q4 sales report final v2.docx" -> "Q4_Sales_Report"

Respond with ONLY the description, nothing else."""

        try:
            response = self._llm_router.generate(
                prompt,
                task="bulk_categorization",
                quality="fast",
                force_local=True
            )
            description = response.content.strip()
            # Validate
            if re.match(r'^[A-Z][a-zA-Z_]+$', description):
                return description
        except Exception as e:
            logger.debug(f"LLM extraction failed: {e}")

        return self._rule_extract_description(path)

    def _generate_reason(self, path: Path, file_type: FileType) -> str:
        """Generate reason for rename."""
        reasons = []

        if file_type == FileType.UNKNOWN:
            reasons.append("unknown file type")

        if not re.search(r'\d{4}-\d{2}-\d{2}', path.name):
            reasons.append("missing ISO date")

        if '_' not in path.name or ' ' in path.name:
            reasons.append("wrong separators")

        if not reasons:
            reasons.append("format standardization")

        return ", ".join(reasons)

    def preview(self, result: ScanResult) -> str:
        """Generate preview text for scan result."""
        lines = [
            f"=== File Organizer Scan ===",
            f"Folder: {result.folder}",
            f"",
            f"Summary:",
            f"  Total files:       {result.total_files}",
            f"  Already compliant: {result.already_compliant}",
            f"  Need rename:       {result.needs_rename}",
            f"  Compliance rate:   {result.compliance_rate:.0%}",
            f"",
        ]

        if result.errors:
            lines.append(f"Errors ({len(result.errors)}):")
            for err in result.errors[:5]:
                lines.append(f"  ! {err}")
            if len(result.errors) > 5:
                lines.append(f"  ... and {len(result.errors) - 5} more")
            lines.append("")

        if result.proposals:
            lines.append("Proposed renames:")
            lines.append("")

            for i, p in enumerate(result.proposals, 1):
                flag = " [!]" if p.needs_review else ""
                lines.append(f"{i:3}. {p.original_name}")
                lines.append(f"     -> {p.new_name}{flag}")
                lines.append(f"        Type: {p.file_type.value}, Confidence: {p.confidence:.0%}")
                lines.append("")
        else:
            lines.append("All files are compliant!")

        return "\n".join(lines)

    def apply(
        self,
        result: ScanResult,
        dry_run: bool = True,
        skip_review: bool = False
    ) -> dict:
        """
        Apply renames.

        Args:
            result: Scan result with proposals
            dry_run: If True, only preview without actual changes
            skip_review: If True, skip files marked needs_review

        Returns:
            Summary dict with renamed, skipped, failed counts
        """
        summary = {
            'renamed': [],
            'skipped': [],
            'failed': [],
            'dry_run': dry_run
        }

        for proposal in result.proposals:
            # Skip if needs review and not forced
            if proposal.needs_review and not skip_review:
                summary['skipped'].append({
                    'file': proposal.original_name,
                    'reason': 'needs manual review'
                })
                continue

            if dry_run:
                summary['renamed'].append({
                    'from': proposal.original_name,
                    'to': proposal.new_name,
                    'action': 'would rename'
                })
            else:
                try:
                    # Perform actual rename
                    shutil.move(
                        str(proposal.original_path),
                        str(proposal.new_path)
                    )
                    summary['renamed'].append({
                        'from': proposal.original_name,
                        'to': proposal.new_name,
                        'action': 'renamed'
                    })
                    logger.info(f"Renamed: {proposal.original_name} -> {proposal.new_name}")
                except Exception as e:
                    summary['failed'].append({
                        'file': proposal.original_name,
                        'error': str(e)
                    })
                    logger.error(f"Failed to rename {proposal.original_name}: {e}")

        return summary

    def apply_summary(self, summary: dict) -> str:
        """Generate summary text for apply result."""
        mode = "DRY RUN" if summary['dry_run'] else "APPLIED"
        lines = [
            f"=== File Organizer - {mode} ===",
            f"",
            f"Renamed: {len(summary['renamed'])}",
            f"Skipped: {len(summary['skipped'])}",
            f"Failed:  {len(summary['failed'])}",
            f"",
        ]

        if summary['renamed']:
            lines.append("Changes:")
            for item in summary['renamed'][:10]:
                lines.append(f"  {item['from']}")
                lines.append(f"    -> {item['to']}")
            if len(summary['renamed']) > 10:
                lines.append(f"  ... and {len(summary['renamed']) - 10} more")

        if summary['failed']:
            lines.append("")
            lines.append("Failures:")
            for item in summary['failed']:
                lines.append(f"  {item['file']}: {item['error']}")

        return "\n".join(lines)


# CLI convenience functions
def scan_inbox(use_llm: bool = False) -> ScanResult:
    """Scan the inbox folder."""
    organizer = FileOrganizer(use_llm=use_llm)
    settings = get_settings()
    return organizer.scan(settings.inbox_path, recursive=True)


def scan_recordings(use_llm: bool = False) -> ScanResult:
    """Scan recordings in inbox."""
    organizer = FileOrganizer(use_llm=use_llm)
    settings = get_settings()
    return organizer.scan(
        settings.inbox_recordings_path,
        extensions=['.mkv', '.mp4', '.m4a', '.mp3', '.webm']
    )


if __name__ == "__main__":
    import sys

    # Parse args
    use_llm = '--llm' in sys.argv
    apply_changes = '--apply' in sys.argv

    # Get folder from args or use inbox
    folder = None
    for arg in sys.argv[1:]:
        if not arg.startswith('--'):
            folder = Path(arg)
            break

    if folder is None:
        folder = get_settings().inbox_path

    print(f"Scanning: {folder}")
    print(f"Using LLM: {use_llm}")
    print()

    organizer = FileOrganizer(use_llm=use_llm)
    result = organizer.scan(folder, recursive=True)

    print(organizer.preview(result))

    if result.proposals:
        if apply_changes:
            summary = organizer.apply(result, dry_run=False)
        else:
            summary = organizer.apply(result, dry_run=True)

        print()
        print(organizer.apply_summary(summary))

        if not apply_changes:
            print()
            print("Run with --apply to actually rename files.")
