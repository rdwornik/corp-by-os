"""
FileOrganizer Agent - LLM-powered file organization.

Location: src/agents/file_organizer.py

Uses:
- qwen2.5:7b for content understanding
- deepseek-r1:1.5b for categorization and reasoning

Naming convention: [TYPE]_[Description]_[YYYY-MM-DD]_[vNN].ext
"""

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

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


class DestinationFolder(Enum):
    """Target folders based on PROJECT_CONTEXT.md structure."""

    INBOX_RECORDINGS = "00_Inbox/recordings"
    INBOX_DOCUMENTS = "00_Inbox/documents"
    INBOX_EMAILS = "00_Inbox/emails"
    PROJECTS = "10_Projects"
    KNOWLEDGE = "20_Knowledge"
    TEMPLATES = "30_Templates"
    ARCHIVE = "80_Archive"


@dataclass
class FileAnalysis:
    """Result of LLM content analysis."""

    summary: str
    file_type: FileType
    suggested_name: str
    destination: DestinationFolder
    project_name: str | None  # e.g., "Honda_PALOMA" if project-related
    reasoning: str
    confidence: float
    content_preview: str = ""


@dataclass
class RenameProposal:
    """Proposed rename and move for a file."""

    original_path: Path
    new_name: str
    new_path: Path
    destination: DestinationFolder
    file_type: FileType
    summary: str
    reasoning: str
    confidence: float
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
    def needs_action(self) -> int:
        return len(self.proposals)


class ContentReader:
    """Reads content from various file formats."""

    SUPPORTED_EXTENSIONS = {".txt", ".md", ".docx", ".pdf"}
    MAX_CONTENT_LENGTH = 8000  # Characters to send to LLM

    @classmethod
    def can_read(cls, path: Path) -> bool:
        return path.suffix.lower() in cls.SUPPORTED_EXTENSIONS

    @classmethod
    def read(cls, path: Path) -> str:
        """Read file content, return empty string if unsupported."""
        ext = path.suffix.lower()

        try:
            if ext in {".txt", ".md"}:
                return cls._read_text(path)
            elif ext == ".docx":
                return cls._read_docx(path)
            elif ext == ".pdf":
                return cls._read_pdf(path)
        except Exception as e:
            logger.warning(f"Failed to read {path.name}: {e}")

        return ""

    @classmethod
    def _read_text(cls, path: Path) -> str:
        """Read plain text file."""
        encodings = ["utf-8", "cp1250", "cp1252", "latin-1"]
        for enc in encodings:
            try:
                content = path.read_text(encoding=enc)
                return content[: cls.MAX_CONTENT_LENGTH]
            except UnicodeDecodeError:
                continue
        return ""

    @classmethod
    def _read_docx(cls, path: Path) -> str:
        """Read Word document."""
        try:
            from docx import Document

            doc = Document(str(path))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            content = "\n".join(paragraphs)
            return content[: cls.MAX_CONTENT_LENGTH]
        except ImportError:
            logger.warning("python-docx not installed, skipping docx")
            return ""

    @classmethod
    def _read_pdf(cls, path: Path) -> str:
        """Read PDF document."""
        try:
            import pypdf

            reader = pypdf.PdfReader(str(path))
            text_parts = []
            for page in reader.pages[:10]:  # First 10 pages
                text_parts.append(page.extract_text() or "")
            content = "\n".join(text_parts)
            return content[: cls.MAX_CONTENT_LENGTH]
        except ImportError:
            logger.warning("pypdf not installed, skipping pdf")
            return ""


class NamingConvention:
    """
    File naming convention logic.

    Format: [TYPE]_[Description]_[YYYY-MM-DD]_[vNN].ext
    """

    VALID_PATTERN = re.compile(
        r"^([A-Z][a-zA-Z]+)"  # TYPE (PascalCase)
        r"_([A-Z][a-zA-Z0-9_]+)"  # Description (PascalCase with _, allows digits)
        r"_(\d{4}-\d{2}-\d{2})"  # Date (YYYY-MM-DD)
        r"(?:_v(\d{2}))?"  # Version (optional)
        r"\.([a-zA-Z0-9]+)$"  # Extension
    )

    EXTENSION_MAP = {
        ".mkv": FileType.RECORDING,
        ".mp4": FileType.RECORDING,
        ".webm": FileType.RECORDING,
        ".m4a": FileType.RECORDING,
        ".mp3": FileType.RECORDING,
        ".wav": FileType.RECORDING,
        ".pptx": FileType.PRESENTATION,
        ".ppt": FileType.PRESENTATION,
        ".docx": FileType.DOCUMENT,
        ".doc": FileType.DOCUMENT,
        ".pdf": FileType.DOCUMENT,
        ".txt": FileType.DOCUMENT,
        ".md": FileType.MEETING_NOTES,
        ".drawio": FileType.DIAGRAM,
        ".vsdx": FileType.DIAGRAM,
        ".png": FileType.SCREENSHOT,
        ".jpg": FileType.SCREENSHOT,
        ".eml": FileType.EMAIL,
        ".msg": FileType.EMAIL,
        ".xlsx": FileType.DOCUMENT,
        ".xls": FileType.DOCUMENT,
    }

    @classmethod
    def is_compliant(cls, filename: str) -> bool:
        return cls.VALID_PATTERN.match(filename) is not None

    @classmethod
    def extract_date(cls, path: Path) -> str:
        """Extract date from filename or file metadata."""
        name = path.stem
        patterns = [
            r"(\d{4}-\d{2}-\d{2})",
            r"(\d{4}_\d{2}_\d{2})",
            r"(\d{2}-\d{2}-\d{4})",
            r"(\d{2}\.\d{2}\.\d{4})",
            r"(\d{8})",
        ]
        for pattern in patterns:
            match = re.search(pattern, name)
            if match:
                return cls._normalize_date(match.group(1))

        try:
            mtime = path.stat().st_mtime
            return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
        except Exception:
            return datetime.now().strftime("%Y-%m-%d")

    @classmethod
    def _normalize_date(cls, date_str: str) -> str:
        date_str = date_str.replace("_", "-").replace(".", "-")
        if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            return date_str
        if re.match(r"^\d{8}$", date_str):
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        parts = date_str.split("-")
        if len(parts) == 3 and len(parts[2]) == 4:
            if int(parts[0]) > 12:
                return f"{parts[2]}-{parts[1]}-{parts[0]}"
            return f"{parts[2]}-{parts[0]}-{parts[1]}"
        return date_str

    @classmethod
    def extract_version(cls, filename: str) -> str | None:
        match = re.search(r"[_\s]v(\d{1,2})", filename, re.I)
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
        version: str | None = None,
    ) -> str:
        """Build compliant filename."""
        # Clean description
        desc = re.sub(r"[^a-zA-Z0-9\s]", "", description)
        words = desc.split()
        words = [w.capitalize() for w in words if w]
        if not words:
            words = ["Untitled"]
        clean_desc = "_".join(words[:5])  # Max 5 words

        parts = [file_type.value, clean_desc, date]
        if version:
            parts.append(version)

        if not extension.startswith("."):
            extension = f".{extension}"

        return f"{'_'.join(parts)}{extension}"


class OllamaClient:
    """Simple Ollama client for LLM calls."""

    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url

    def generate(self, model: str, prompt: str, system: str = "") -> str:
        """Generate text using Ollama API."""
        import urllib.error
        import urllib.request

        url = f"{self.base_url}/api/generate"
        data = {
            "model": model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "options": {"temperature": 0.3},
        }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=120) as response:
                result = json.loads(response.read().decode("utf-8"))
                return result.get("response", "")
        except urllib.error.URLError as e:
            logger.error(f"Ollama connection failed: {e}")
            raise
        except Exception as e:
            logger.error(f"Ollama generate failed: {e}")
            raise

    def is_available(self) -> bool:
        """Check if Ollama is running."""
        import urllib.request

        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=5):
                return True
        except Exception:
            return False


class FileOrganizer:
    """
    LLM-powered file organization agent.

    Uses two models:
    - qwen2.5:7b: Reads and understands file content
    - deepseek-r1:1.5b: Categorizes and reasons about destination

    Usage:
        organizer = FileOrganizer()
        result = organizer.scan(folder_path)
        print(organizer.preview(result))
        organizer.apply(result, dry_run=True)  # Always dry-run first!
    """

    # Folder structure for reasoning
    FOLDER_STRUCTURE = """
Folder structure (from PROJECT_CONTEXT.md):
- 00_Inbox/recordings - new audio/video files awaiting processing
- 00_Inbox/documents - new documents awaiting organization
- 00_Inbox/emails - saved emails awaiting processing
- 10_Projects/{Company_Solution}/ - active opportunities (e.g., Honda_PALOMA, PepsiCo_EMEA)
- 20_Knowledge/ - general knowledge, product info, best practices
- 30_Templates/ - reusable templates
- 80_Archive/{YYYY}/ - completed/old projects by year
"""

    def __init__(self):
        self.settings = get_settings()
        self.ollama = OllamaClient(self.settings.ollama_base_url)
        self._llm_available = None

    @property
    def llm_available(self) -> bool:
        if self._llm_available is None:
            self._llm_available = self.ollama.is_available()
        return self._llm_available

    def scan(
        self, folder: Path, recursive: bool = True, extensions: list[str] | None = None
    ) -> ScanResult:
        """Scan folder and generate organization proposals."""
        folder = Path(folder)
        if not folder.exists():
            return ScanResult(
                folder=folder,
                total_files=0,
                already_compliant=0,
                errors=[f"Folder does not exist: {folder}"],
            )

        if not self.llm_available:
            return ScanResult(
                folder=folder,
                total_files=0,
                already_compliant=0,
                errors=["Ollama not available. Start with: ollama serve"],
            )

        # Collect files
        files = list(folder.rglob("*") if recursive else folder.glob("*"))
        files = [f for f in files if f.is_file()]
        files = [f for f in files if not f.name.startswith((".", "~"))]

        if extensions:
            extensions = [e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions]
            files = [f for f in files if f.suffix.lower() in extensions]

        result = ScanResult(folder=folder, total_files=len(files), already_compliant=0)

        for file_path in files:
            try:
                if NamingConvention.is_compliant(file_path.name):
                    result.already_compliant += 1
                else:
                    proposal = self._analyze_and_propose(file_path)
                    if proposal:
                        result.proposals.append(proposal)
            except Exception as e:
                result.errors.append(f"{file_path.name}: {e}")
                logger.exception(f"Error processing {file_path}")

        return result

    def _analyze_and_propose(self, path: Path) -> RenameProposal | None:
        """Analyze file with LLM and create proposal."""
        # Read content if possible
        content = ""
        if ContentReader.can_read(path):
            content = ContentReader.read(path)

        # Step 1: Understand content with qwen2.5:7b
        understanding = self._understand_content(path, content)

        # Step 2: Categorize with deepseek-r1:1.5b
        analysis = self._categorize_file(path, content, understanding)

        # Build new path
        date = NamingConvention.extract_date(path)
        version = NamingConvention.extract_version(path.name)

        new_name = NamingConvention.build_name(
            file_type=analysis.file_type,
            description=analysis.suggested_name,
            date=date,
            extension=path.suffix,
            version=version,
        )

        # Determine destination path
        dest_path = self._resolve_destination(analysis)
        new_path = dest_path / new_name

        # Handle duplicates
        counter = 1
        while new_path.exists() and new_path != path:
            ver = f"v{counter:02d}"
            new_name = NamingConvention.build_name(
                file_type=analysis.file_type,
                description=analysis.suggested_name,
                date=date,
                extension=path.suffix,
                version=ver,
            )
            new_path = dest_path / new_name
            counter += 1

        return RenameProposal(
            original_path=path,
            new_name=new_name,
            new_path=new_path,
            destination=analysis.destination,
            file_type=analysis.file_type,
            summary=analysis.summary,
            reasoning=analysis.reasoning,
            confidence=analysis.confidence,
            needs_review=analysis.confidence < 0.7,
        )

    def _understand_content(self, path: Path, content: str) -> str:
        """Use qwen2.5:7b to understand file content."""
        if not content:
            return f"File: {path.name} (no readable content)"

        prompt = f"""Analyze this file and provide a brief summary.

Filename: {path.name}
Content (first {len(content)} chars):
---
{content[:4000]}
---

Provide a 2-3 sentence summary of what this file contains.
Focus on: topic, purpose, any company/project names mentioned."""

        try:
            response = self.ollama.generate(
                model=self.settings.ollama_model_reader,
                prompt=prompt,
                system="You are a document analyst. Be concise and factual.",
            )
            return response.strip()
        except Exception as e:
            logger.warning(f"Content understanding failed: {e}")
            return f"File: {path.name}"

    def _categorize_file(self, path: Path, content: str, understanding: str) -> FileAnalysis:
        """Use deepseek-r1:1.5b to categorize and reason about destination."""
        ext = path.suffix.lower()
        default_type = NamingConvention.EXTENSION_MAP.get(ext, FileType.UNKNOWN)

        prompt = f"""Categorize this file and decide where it should go.

Filename: {path.name}
Extension: {ext}
Understanding: {understanding}

{self.FOLDER_STRUCTURE}

File types: MeetingNotes, Recording, Transcript, Presentation,
Document, RFP, Email, Screenshot, Diagram

Respond in this exact JSON format:
{{
    "file_type": "Document",
    "suggested_name": "Brief descriptive name without date",
    "destination": "20_Knowledge",
    "project_name": null,
    "reasoning": "Why this categorization makes sense",
    "confidence": 0.8
}}

Rules:
- If related to a specific company/opportunity, destination
should be "10_Projects" and include project_name as
"Company_Solution"
- Generic knowledge/reference material goes to "20_Knowledge"
- New unprocessed files stay in appropriate 00_Inbox subfolder
- suggested_name should be 2-4 words, PascalCase style (will be formatted later)
- confidence from 0.0 to 1.0"""

        try:
            response = self.ollama.generate(
                model=self.settings.ollama_model_reasoner,
                prompt=prompt,
                system="You are a file organization assistant. Respond only with valid JSON.",
            )

            # Extract JSON from response (handle markdown code blocks)
            json_match = re.search(r"\{[^{}]*\}", response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())

                file_type = FileType.UNKNOWN
                for ft in FileType:
                    if ft.value.lower() == data.get("file_type", "").lower():
                        file_type = ft
                        break

                dest = DestinationFolder.INBOX_DOCUMENTS
                dest_str = data.get("destination", "")
                for d in DestinationFolder:
                    if d.value in dest_str or dest_str in d.value:
                        dest = d
                        break

                return FileAnalysis(
                    summary=understanding,
                    file_type=file_type if file_type != FileType.UNKNOWN else default_type,
                    suggested_name=data.get("suggested_name", path.stem),
                    destination=dest,
                    project_name=data.get("project_name"),
                    reasoning=data.get("reasoning", "LLM categorization"),
                    confidence=float(data.get("confidence", 0.6)),
                )

        except Exception as e:
            logger.warning(f"Categorization failed: {e}")

        # Fallback to rule-based
        return FileAnalysis(
            summary=understanding,
            file_type=default_type,
            suggested_name=path.stem,
            destination=DestinationFolder.INBOX_DOCUMENTS,
            project_name=None,
            reasoning="Fallback: LLM categorization failed",
            confidence=0.3,
        )

    def _resolve_destination(self, analysis: FileAnalysis) -> Path:
        """Resolve destination to actual path."""
        base = self.settings.role_path

        if analysis.destination == DestinationFolder.PROJECTS and analysis.project_name:
            return base / "10_Projects" / analysis.project_name
        elif analysis.destination == DestinationFolder.PROJECTS:
            return base / "10_Projects"

        return base / analysis.destination.value

    def preview(self, result: ScanResult) -> str:
        """Generate detailed preview of proposals."""
        lines = [
            "=" * 60,
            "FILE ORGANIZER - SCAN RESULTS",
            "=" * 60,
            f"Folder: {result.folder}",
            f"Total files: {result.total_files}",
            f"Already compliant: {result.already_compliant}",
            f"Need organization: {result.needs_action}",
            "",
        ]

        if result.errors:
            lines.append(f"ERRORS ({len(result.errors)}):")
            for err in result.errors[:5]:
                lines.append(f"  ! {err}")
            lines.append("")

        if result.proposals:
            lines.append("PROPOSALS:")
            lines.append("-" * 60)

            for i, p in enumerate(result.proposals, 1):
                review_flag = " [REVIEW]" if p.needs_review else ""
                lines.append(f"\n{i}. {p.original_name}{review_flag}")
                lines.append(f"   Summary: {p.summary[:100]}...")
                lines.append(f"   Type: {p.file_type.value}")
                lines.append(f"   New name: {p.new_name}")
                lines.append(f"   Destination: {p.destination.value}")
                lines.append(f"   Confidence: {p.confidence:.0%}")
                lines.append(f"   Reasoning: {p.reasoning}")
        else:
            lines.append("All files are compliant!")

        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)

    def apply(
        self,
        result: ScanResult,
        dry_run: bool = True,
        skip_low_confidence: bool = True,
        min_confidence: float = 0.5,
    ) -> dict:
        """
        Apply organization proposals.

        ALWAYS use dry_run=True first to preview changes!
        """
        summary = {"moved": [], "skipped": [], "failed": [], "dry_run": dry_run}

        for proposal in result.proposals:
            # Skip low confidence
            if skip_low_confidence and proposal.confidence < min_confidence:
                summary["skipped"].append(
                    {
                        "file": proposal.original_name,
                        "reason": f"Low confidence ({proposal.confidence:.0%})",
                    }
                )
                continue

            if dry_run:
                summary["moved"].append(
                    {
                        "from": str(proposal.original_path),
                        "to": str(proposal.new_path),
                        "action": "WOULD MOVE",
                    }
                )
            else:
                try:
                    # Ensure destination exists
                    proposal.new_path.parent.mkdir(parents=True, exist_ok=True)

                    # Move file
                    shutil.move(str(proposal.original_path), str(proposal.new_path))

                    summary["moved"].append(
                        {
                            "from": str(proposal.original_path),
                            "to": str(proposal.new_path),
                            "action": "MOVED",
                        }
                    )
                    logger.info(f"Moved: {proposal.original_name} -> {proposal.new_path}")
                except Exception as e:
                    summary["failed"].append({"file": proposal.original_name, "error": str(e)})

        return summary

    def apply_summary(self, summary: dict) -> str:
        """Generate summary of apply operation."""
        mode = "DRY RUN" if summary["dry_run"] else "APPLIED"
        lines = [
            "=" * 60,
            f"FILE ORGANIZER - {mode}",
            "=" * 60,
            f"Moved: {len(summary['moved'])}",
            f"Skipped: {len(summary['skipped'])}",
            f"Failed: {len(summary['failed'])}",
            "",
        ]

        if summary["moved"]:
            lines.append("CHANGES:")
            for item in summary["moved"][:10]:
                lines.append(f"  {item['action']}: {Path(item['from']).name}")
                lines.append(f"    -> {item['to']}")
            if len(summary["moved"]) > 10:
                lines.append(f"  ... and {len(summary['moved']) - 10} more")

        if summary["skipped"]:
            lines.append("\nSKIPPED:")
            for item in summary["skipped"][:5]:
                lines.append(f"  {item['file']}: {item['reason']}")

        if summary["failed"]:
            lines.append("\nFAILED:")
            for item in summary["failed"]:
                lines.append(f"  {item['file']}: {item['error']}")

        if summary["dry_run"]:
            lines.append("\n" + "=" * 60)
            lines.append("This was a DRY RUN. No files were moved.")
            lines.append("To apply changes, run with dry_run=False")
            lines.append("=" * 60)

        return "\n".join(lines)


def scan_inbox() -> ScanResult:
    """Scan the inbox folder with LLM analysis."""
    organizer = FileOrganizer()
    settings = get_settings()
    return organizer.scan(settings.inbox_path, recursive=True)


if __name__ == "__main__":
    import sys

    apply_flag = "--apply" in sys.argv

    folder = None
    for arg in sys.argv[1:]:
        if not arg.startswith("--"):
            folder = Path(arg)
            break

    if folder is None:
        folder = get_settings().inbox_path

    print(f"Scanning: {folder}")
    print("Using models: qwen2.5:7b (reader), deepseek-r1:1.5b (reasoner)")
    print()

    organizer = FileOrganizer()

    if not organizer.llm_available:
        print("ERROR: Ollama not available!")
        print("Start Ollama with: ollama serve")
        print("Then pull models: ollama pull qwen2.5:7b && ollama pull deepseek-r1:1.5b")
        sys.exit(1)

    result = organizer.scan(folder)
    print(organizer.preview(result))

    if result.proposals:
        summary = organizer.apply(result, dry_run=not apply_flag)
        print()
        print(organizer.apply_summary(summary))
