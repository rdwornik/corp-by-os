# Corporate OS

Universal Agent-Based Automation for Corporate Knowledge Work.

## Features

- **Hybrid LLM** - Local (Ollama) for sensitive data, Cloud (Claude/Gemini) for quality
- **Privacy-First** - Sensitivity check always runs locally
- **Agent Architecture** - Modular agents for different tasks
- **Microsoft Integration** - Graph API for email, calendar, OneNote

## Quick Start

```bash
# Clone (outside OneDrive!)
git clone <repo> C:\Dev\corporate-os
cd C:\Dev\corporate-os

# Setup
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env with your API keys

# Test
python -c "from config import settings; print(settings.role_path)"
```

## OneDrive Structure

```
OneDrive - Blue Yonder/
└── MyWork/
    └── 00_Tech_PreSales/
        ├── 00_Inbox/
        ├── 10_Projects/
        ├── 20_Knowledge/
        ├── 30_Templates/
        ├── 80_Archive/
        └── 90_System/
```

## Usage

```bash
# Search knowledge
corp search "WMS implementation"

# Generate brief
corp brief "PepsiCo Technical Review"

# Process inbox
corp process
```

## License

MIT
