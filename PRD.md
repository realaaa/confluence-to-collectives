# PRD: Confluence Cloud → Nextcloud Collectives Migration CLI

## Overview

CLI tool (`migrate.py`) to migrate pages and attachments from Confluence Cloud to Nextcloud Collectives. Single-file Python application using Click with a three-phase pipeline: export → convert → upload.

## Goals

- Migrate Confluence pages preserving hierarchy, content, images, attachments, and comments
- Produce clean Markdown compatible with Nextcloud Collectives rendering
- Support incremental/resumable migrations with per-page state tracking
- Provide dry-run mode for safe preview of all operations

## Architecture

### Pipeline Phases

1. **Export** — Fetch pages, comments, and attachments from Confluence Cloud REST API v2 to local disk
2. **Convert** — Transform Confluence HTML to Markdown, rewrite image references, build output directory tree
3. **Upload** — Push converted Markdown and attachments to Nextcloud Collectives via WebDAV

### Classes

| Class | Responsibility |
|-------|---------------|
| `MigrationState` | JSON persistence of per-page migration status |
| `ConfluenceClient` | Confluence Cloud REST API v2 client with auth, pagination, rate limiting |
| `Converter` | HTML preprocessing + html2text conversion + tree building |
| `NextcloudClient` | WebDAV client for Nextcloud file operations |

### CLI Commands

| Command | Description |
|---------|-------------|
| `migrate` | Full pipeline: export → convert → upload |
| `export` | Export from Confluence to local `export_data/` |
| `convert` | Convert exported HTML to Markdown in `convert_data/` |
| `upload` | Upload converted files to Nextcloud Collectives |
| `status` | Show migration progress summary |

## Scope Options

- `--space SPACE_KEY` — Migrate a specific Confluence space
- `--pages ID1,ID2` — Migrate specific page IDs
- `--all-spaces` — Migrate all accessible spaces

## Standard Options

- `--dry-run` — Preview without writing
- `--debug` — Enable debug logging
- `--log-file PATH` — Custom log file path
- `--exclude-images` — Skip image attachments
- `--exclude-attachments` — Skip all attachments
- `--target-parent NAME` — Parent page in Collectives (default: `MigratedPages`)

## Content Mapping

| Confluence Element | Markdown Output |
|-------------------|-----------------|
| Page body (export_view HTML) | Markdown via html2text |
| Tables (including block elements in cells) | Markdown tables (headers simplified) |
| Images | `![alt](image.png)` with relative paths |
| Code blocks | Fenced code blocks with language hints |
| Info/warning/note panels | Blockquotes with bold prefix |
| User mentions | `@DisplayName` |
| Internal links | Relative markdown links |
| Unsupported macros | `<!-- Unsupported macro: name -->` |
| Footer comments | `## Comments` section with author/date |
| Non-image attachments | `## Attachments` section with links |

## Directory Structure

### Export (intermediate)
```
export_data/{space_key}/
  pages/{page_id}.json
  attachments/{page_id}/{filename}
```

### Convert (output)
```
convert_data/{space_key}/
  Readme.md                  # space homepage
  ParentPage/
    Readme.md                # parent content
    image.png                # parent attachment
    ChildPage.md             # leaf child
    child-image.png          # child attachment
```

### Page-to-path Rules
- Space homepage → `Readme.md` (root)
- Page with children → `{title}/Readme.md`
- Leaf page → `{title}.md`
- Filename sanitization: strip `/ \ : * ? " < > |`, cap 200 chars, dedupe `-2` suffix

## State Management

JSON file `.migration-state.json` with per-page records:
```json
{
  "page_id": "12345",
  "title": "Page Title",
  "space_key": "SPACE",
  "parent_id": "12344",
  "has_children": false,
  "status": "exported",
  "export_path": "export_data/SPACE/pages/12345.json",
  "convert_path": null,
  "upload_path": null,
  "error": null,
  "attachments": [],
  "comments": []
}
```

Status flow: `pending → exported → converted → uploaded | failed`

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All pages migrated successfully |
| 1 | Partial failure (some pages failed) |
| 2 | Complete failure |
| 3 | Configuration error |
| 4 | Authentication error |

## Configuration

Environment variables via `.env`:
- `CONFLUENCE_BASE_URL`, `CONFLUENCE_USERNAME`, `CONFLUENCE_API_TOKEN`
- `NEXTCLOUD_URL`, `NEXTCLOUD_USERNAME`, `NEXTCLOUD_PASSWORD`, `NEXTCLOUD_COLLECTIVE`

## Non-Goals (MVP)

- config.yaml support
- Parallel/async downloads
- Confluence Server/Data Center support
- MCP server integration in the tool itself
- Page permission migration
