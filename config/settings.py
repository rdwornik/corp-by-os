"""
Settings management using Pydantic.

Location: config/settings.py
"""

from pathlib import Path
from typing import Optional, Literal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, computed_field


class Settings(BaseSettings):
    """Application settings."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CORP_",
        extra="ignore"
    )
    
    # ===================
    # PATHS
    # ===================
    onedrive_path: Path = Field(
        default=Path("C:/Users/1028120/OneDrive - Blue Yonder"),
        description="OneDrive root"
    )
    repo_path: Path = Field(
        default=Path("C:/Dev/corporate-os"),
        description="Repository root (outside OneDrive)"
    )
    
    # MyWork structure
    mywork_folder: str = "MyWork"
    current_role_folder: str = "00_Tech_PreSales"
    
    # Subfolders
    inbox_folder: str = "00_Inbox"
    projects_folder: str = "10_Projects"
    knowledge_folder: str = "20_Knowledge"
    templates_folder: str = "30_Templates"
    archive_folder: str = "80_Archive"
    system_folder: str = "90_System"
    
    # ===================
    # LLM
    # ===================
    claude_model: str = "claude-sonnet-4-20250514"
    haiku_model: str = "claude-haiku-4-5-20251001"
    deepseek_model: str = "deepseek-chat"
    gemini_model: str = "gemini-1.5-pro"

    # API Keys (from .env)
    anthropic_api_key: Optional[str] = Field(default=None, alias="ANTHROPIC_API_KEY")
    deepseek_api_key: Optional[str] = Field(default=None, alias="DEEPSEEK_API_KEY")
    google_api_key: Optional[str] = Field(default=None, alias="GOOGLE_API_KEY")
    graph_access_token: Optional[str] = Field(default=None, alias="GRAPH_ACCESS_TOKEN")
    
    # ===================
    # WHISPER
    # ===================
    whisper_model: Literal["tiny", "base", "small", "medium", "large"] = "base"
    whisper_language: str = "en"
    
    # ===================
    # FILE TYPES
    # ===================
    audio_extensions: list[str] = Field(
        default=[".mkv", ".m4a", ".mp3", ".wav", ".webm", ".mp4"]
    )
    document_extensions: list[str] = Field(
        default=[".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md"]
    )
    
    # ===================
    # LOGGING
    # ===================
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    
    # ===================
    # COMPUTED PATHS
    # ===================
    @computed_field
    @property
    def mywork_path(self) -> Path:
        return self.onedrive_path / self.mywork_folder
    
    @computed_field
    @property
    def role_path(self) -> Path:
        """Current role root."""
        return self.mywork_path / self.current_role_folder
    
    @computed_field
    @property
    def inbox_path(self) -> Path:
        return self.role_path / self.inbox_folder
    
    @computed_field
    @property
    def inbox_recordings_path(self) -> Path:
        return self.inbox_path / "recordings"
    
    @computed_field
    @property
    def inbox_documents_path(self) -> Path:
        return self.inbox_path / "documents"
    
    @computed_field
    @property
    def inbox_emails_path(self) -> Path:
        return self.inbox_path / "emails"
    
    @computed_field
    @property
    def projects_path(self) -> Path:
        return self.role_path / self.projects_folder
    
    @computed_field
    @property
    def knowledge_path(self) -> Path:
        return self.role_path / self.knowledge_folder
    
    @computed_field
    @property
    def templates_path(self) -> Path:
        return self.role_path / self.templates_folder
    
    @computed_field
    @property
    def archive_path(self) -> Path:
        return self.role_path / self.archive_folder
    
    @computed_field
    @property
    def system_path(self) -> Path:
        return self.role_path / self.system_folder
    
    @computed_field
    @property
    def index_path(self) -> Path:
        return self.system_path / "index"
    
    @computed_field
    @property
    def chroma_path(self) -> Path:
        return self.index_path / "chroma"
    
    @computed_field
    @property
    def logs_path(self) -> Path:
        return self.system_path / "logs"
    
    @computed_field
    @property
    def cache_path(self) -> Path:
        return self.system_path / "cache"
    
    @computed_field
    @property
    def briefs_path(self) -> Path:
        return self.system_path / "briefs"
    
    @computed_field
    @property
    def prompts_path(self) -> Path:
        return self.repo_path / "config" / "prompts"
    
    # ===================
    # HELPERS
    # ===================
    def get_project_template_path(self) -> Path:
        return self.projects_path / "_template"
    
    def get_archive_year_path(self, year: int) -> Path:
        return self.archive_path / str(year)
    
    def is_audio_file(self, path: Path) -> bool:
        return path.suffix.lower() in self.audio_extensions
    
    def is_document_file(self, path: Path) -> bool:
        return path.suffix.lower() in self.document_extensions
    
    def ensure_paths_exist(self) -> None:
        """Create required directories."""
        paths = [
            self.inbox_recordings_path,
            self.inbox_documents_path,
            self.inbox_emails_path,
            self.projects_path,
            self.knowledge_path,
            self.templates_path,
            self.archive_path,
            self.index_path,
            self.chroma_path,
            self.logs_path,
            self.cache_path,
            self.briefs_path,
        ]
        for p in paths:
            p.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


if __name__ == "__main__":
    s = get_settings()
    print("=== Corporate OS Settings ===\n")
    print(f"Repo:       {s.repo_path}")
    print(f"OneDrive:   {s.onedrive_path}")
    print(f"MyWork:     {s.mywork_path}")
    print(f"Role:       {s.role_path}")
    print(f"\nWork Paths:")
    print(f"  Inbox:      {s.inbox_path}")
    print(f"  Projects:   {s.projects_path}")
    print(f"  Knowledge:  {s.knowledge_path}")
    print(f"  Templates:  {s.templates_path}")
    print(f"  Archive:    {s.archive_path}")
    print(f"  System:     {s.system_path}")
    print(f"\nLLM:")
    print(f"  Ollama:     {s.ollama_base_url}")
    print(f"  Cloud:      {s.default_cloud_provider}")
    print(f"  Claude:     {'✓' if s.anthropic_api_key else '✗'}")
    print(f"  Gemini:     {'✓' if s.google_api_key else '✗'}")
    print(f"  Graph:      {'✓' if s.graph_access_token else '✗'}")
