# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CLI tool to migrate content from Confluence Cloud to Nextcloud Collectives. Single-file Python application (`migrate.py`) using Click for CLI, with three-phase pipeline: export → convert → upload.

## Commands

```bash
# Setup
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run migrations
python migrate.py migrate --space-id SPACE_KEY    # Full pipeline
python migrate.py export --space-id SPACE_KEY     # Export only
python migrate.py convert                          # Convert HTML to Markdown
python migrate.py upload                           # Upload to Nextcloud

# Other commands
python migrate.py status                           # Show migration progress
python migrate.py reset --reset-failed             # Reset failed pages to retry
```

## Architecture

**migrate.py** contains all logic in a single file:

- **Configuration** (lines ~50-150): Dataclasses for config, `load_config()` with `${ENV_VAR}` substitution, loads from `.env` via python-dotenv
- **State Management** (lines ~150-220): `PageState`/`MigrationState` dataclasses, JSON persistence in `.migration-state.json`
- **ConfluenceClient** (lines ~220-400): REST API client for Confluence Cloud v2 API, handles space key→ID resolution, pagination, attachments
- **Converter** (lines ~400-500): `convert_html_to_markdown()` with macro handling (info/warning panels→blockquotes, code→fenced blocks, TOC removal)
- **NextcloudClient** (lines ~500-580): WebDAV client for Collectives, uploads to `Collectives/{collective}/` path
- **CLI Commands** (lines ~600-end): Click commands - export, convert, upload, migrate, status, reset

**Key patterns:**
- Space IDs can be keys (e.g., "WGS") or numeric - `resolve_space_id()` handles lookup
- State tracks per-page status: pending → exported → converted → uploaded (or failed)
- Retries with exponential backoff on network errors, respects rate limits
- Parent pages with children use `Readme.md`, leaf pages use `{title}.md`
- Attachments go in `.attachments.{page_id}/` directories

## Configuration

Credentials via `.env` file (auto-loaded) or environment variables:
- `CONFLUENCE_BASE_URL`, `CONFLUENCE_USERNAME`, `CONFLUENCE_API_TOKEN`
- `NEXTCLOUD_SERVER`, `NEXTCLOUD_USERNAME`, `NEXTCLOUD_PASSWORD`, `NEXTCLOUD_COLLECTIVE`

Config file `config.yaml` supports `${ENV_VAR}` substitution.

## Exit Codes

- 0: Success
- 1: Partial failure (some pages failed)
- 2: Complete failure
- 3: Config error
- 4: Auth error
