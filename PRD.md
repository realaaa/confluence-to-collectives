# Product Requirements Document: Confluence to Nextcloud Collectives Migration Tool

## 1. Executive Summary

This document specifies the requirements for a Python-based command-line tool that migrates content from Confluence Cloud to Nextcloud Collectives. The tool exports pages and attachments from Confluence, converts HTML content to Markdown, and uploads the result to Nextcloud Collectives via WebDAV while preserving the original page hierarchy.

## 2. Goals & Non-Goals

### Goals
- Provide a reliable, resumable migration path from Confluence Cloud to Nextcloud Collectives
- Preserve page hierarchy and content structure during migration
- Handle attachments including images and documents
- Support partial migrations (specific pages or spaces)
- Enable dry-run previews before actual migration
- Log all operations for audit and troubleshooting

### Non-Goals
- Real-time synchronization between platforms
- Bidirectional sync (Collectives back to Confluence)
- Migration of Confluence permissions/access controls
- Support for Confluence Server/Data Center (Cloud only)
- Migration of page comments or version history
- Support for Confluence macros with dynamic content (e.g., Jira issues, charts)

## 3. User Stories

1. **As a team admin**, I want to migrate our entire Confluence space to Collectives so that we can move away from Confluence.

2. **As a content manager**, I want to migrate specific pages without affecting others so that I can do incremental migrations.

3. **As a migration operator**, I want to preview what will be migrated without making changes so that I can verify the scope.

4. **As a migration operator**, I want failed pages to be skipped with logging so that one failure doesn't stop the entire migration.

5. **As a migration operator**, I want to resume a failed migration from where it stopped so that I don't have to restart from scratch.

## 4. Technical Requirements

### 4.1 Source System: Confluence Cloud

**API Version:** REST API v2

**Endpoints:**
| Purpose | Endpoint |
|---------|----------|
| List spaces | `GET /wiki/api/v2/spaces` |
| List pages | `GET /wiki/api/v2/pages` |
| Get page content | `GET /wiki/rest/api/content/{id}?expand=body.export_view` |
| Get attachments | `GET /wiki/rest/api/content/{id}/child/attachment` |
| Download attachment | `GET /wiki/rest/api/content/{id}/child/attachment/{attachmentId}/download` |

**Authentication:**
- Method: HTTP Basic Auth
- Username: User's email address
- Password: API token (generated from Atlassian account settings)

**Rate Limiting:**
- Respect `Retry-After` headers
- Implement exponential backoff on 429 responses
- Default delay: 100ms between requests

### 4.2 Target System: Nextcloud Collectives

**Protocol:** WebDAV

**Base Endpoint:** `https://{server}/remote.php/dav/files/{username}/`

**Path Structure:**
```
{collective_name}/
├── Page Name.md
├── Parent Page/
│   ├── Readme.md           # Parent page content
│   └── Child Page.md
└── .attachments.{page_id}/
    ├── image1.png
    └── document.pdf
```

**Authentication:**
- Method: HTTP Basic Auth
- Username: Nextcloud username
- Password: App password (recommended) or user password

**Page Creation:**
- Create directories for pages with children
- Create `.md` files for page content
- Parent page content goes in `Readme.md` within its directory

### 4.3 Data Transformation

**HTML to Markdown Conversion:**
- Use `markdownify` library as primary converter
- Preserve headings (h1-h6)
- Convert tables to Markdown tables
- Convert lists (ordered and unordered)
- Preserve code blocks with language hints where available
- Convert inline formatting (bold, italic, strikethrough)

**Confluence-Specific Handling:**
| Confluence Element | Markdown Conversion |
|-------------------|---------------------|
| `<ac:structured-macro>` | Remove or convert to text equivalent |
| `<ac:image>` | Convert to `![alt](path)` |
| `<ac:link>` | Convert to `[text](url)` |
| Info/Warning/Note panels | Blockquote with prefix |
| Code macro | Fenced code block |
| Table of Contents macro | Remove (Collectives auto-generates) |

**Image Reference Rewriting:**
- Original: `<img src="/wiki/download/attachments/123/image.png">`
- Converted: `![image](/.attachments.{page_id}/image.png)`

## 5. Functional Requirements

### 5.1 Export Phase

**Input:** Confluence credentials, space ID(s) or page ID(s)

**Process:**
1. Authenticate with Confluence API
2. Retrieve page tree structure for specified scope
3. For each page:
   - Download HTML content (export view)
   - Download all attachments
   - Record parent-child relationships
4. Save to local directory structure

**Output:** Local directory with:
```
export/
├── pages/
│   └── {page_id}/
│       ├── content.html
│       ├── metadata.json
│       └── attachments/
│           └── {filename}
└── tree.json
```

**Metadata JSON Schema:**
```json
{
  "id": "string",
  "title": "string",
  "parentId": "string|null",
  "spaceId": "string",
  "created": "ISO8601",
  "modified": "ISO8601",
  "author": "string",
  "attachments": [
    {
      "id": "string",
      "filename": "string",
      "mediaType": "string",
      "size": "number"
    }
  ]
}
```

### 5.2 Convert Phase

**Input:** Local export directory

**Process:**
1. Load page tree structure
2. For each page:
   - Read HTML content
   - Convert to Markdown
   - Rewrite attachment references
   - Add metadata header (optional)
3. Generate Collectives-compatible directory structure

**Output:** Local directory with:
```
converted/
├── Page Title.md
└── Parent Page/
    ├── Readme.md
    ├── Child Page.md
    └── .attachments.{id}/
        └── image.png
```

**Metadata Header Format:**
```markdown
---
migrated_from: confluence
original_id: "123456"
original_space: "SPACE"
migrated_at: "2024-01-15T10:30:00Z"
original_author: "user@example.com"
---

# Page Title

Content starts here...
```

### 5.3 Upload Phase

**Input:** Converted directory, Nextcloud credentials, target collective

**Process:**
1. Authenticate with Nextcloud WebDAV
2. Create target parent directory if specified
3. For each page (respecting hierarchy):
   - Check if page exists (skip if conflict resolution = skip)
   - Create directory if page has children
   - Upload `.md` file
   - Upload attachments to `.attachments.{id}/` directory
4. Update state file after each successful upload

**Conflict Resolution:** Skip existing pages (do not overwrite)

### 5.4 State Management

**State File:** `.migration-state.json`

**Schema:**
```json
{
  "version": "1.0",
  "started_at": "ISO8601",
  "updated_at": "ISO8601",
  "source": {
    "type": "confluence",
    "base_url": "string",
    "space_ids": ["string"]
  },
  "target": {
    "type": "collectives",
    "server": "string",
    "collective": "string"
  },
  "pages": {
    "{page_id}": {
      "status": "pending|exported|converted|uploaded|failed",
      "error": "string|null",
      "exported_at": "ISO8601|null",
      "converted_at": "ISO8601|null",
      "uploaded_at": "ISO8601|null"
    }
  }
}
```

**Resume Behavior:**
- On startup, load existing state file if present
- Skip pages already in target status for current phase
- Retry pages with `failed` status
- Save state after each page completes

## 6. CLI Interface Specification

### Command Structure

```
confluence-to-collectives <command> [options]
```

### Subcommands

| Command | Description |
|---------|-------------|
| `export` | Export pages from Confluence to local HTML |
| `convert` | Convert local HTML to Markdown |
| `upload` | Upload converted Markdown to Collectives |
| `migrate` | Run full pipeline (export → convert → upload) |

### Global Options

| Option | Short | Description |
|--------|-------|-------------|
| `--help` | `-h` | Show help message |
| `--config <path>` | `-c` | Path to config file |
| `--dry-run` | | Preview changes without executing |
| `--debug` | `-v` | Enable verbose logging |
| `--log-file <path>` | | Custom log file path |

### Export/Migrate Options

| Option | Description |
|--------|-------------|
| `--space-id <id>` | Export specific space |
| `--page-ids <id1,id2,...>` | Export specific pages (comma-separated) |
| `--all` | Export all accessible content |
| `--exclude-images` | Skip image attachments |
| `--exclude-attachments` | Skip all attachments |

### Upload/Migrate Options

| Option | Description |
|--------|-------------|
| `--target-parent <name>` | Parent page name in Collectives (default: `MigratedPages`) |

### Examples

```bash
# Export a single space
confluence-to-collectives export --space-id TEAM --config config.yaml

# Convert with dry-run
confluence-to-collectives convert --dry-run

# Full migration
confluence-to-collectives migrate --space-id DOCS --target-parent "Legacy Docs"

# Migrate specific pages
confluence-to-collectives migrate --page-ids 12345,67890
```

## 7. Configuration Schema

**File:** `config.yaml`

```yaml
# Confluence Cloud settings
confluence:
  base_url: "https://yourcompany.atlassian.net"
  username: "user@example.com"
  api_token: "${CONFLUENCE_API_TOKEN}"  # Supports env var substitution

# Nextcloud settings
nextcloud:
  server: "https://cloud.example.com"
  username: "admin"
  password: "${NEXTCLOUD_PASSWORD}"
  collective: "My Collective"

# Migration settings
migration:
  output_dir: "./migration-data"
  max_file_size_mb: 100
  include_metadata: true
  target_parent: "MigratedPages"

# Logging settings
logging:
  level: "INFO"  # DEBUG, INFO, WARNING, ERROR
  file: "./migration.log"
```

**Environment Variables:**

| Variable | Description |
|----------|-------------|
| `CONFLUENCE_API_TOKEN` | Confluence API token |
| `CONFLUENCE_USERNAME` | Confluence username (email) |
| `CONFLUENCE_BASE_URL` | Confluence instance URL |
| `NEXTCLOUD_PASSWORD` | Nextcloud password/app password |
| `NEXTCLOUD_USERNAME` | Nextcloud username |
| `NEXTCLOUD_SERVER` | Nextcloud server URL |

**Precedence:** CLI options > Environment variables > Config file

## 8. Error Handling Strategy

### Approach: Skip and Continue

Failed pages are logged and skipped. Migration continues with remaining pages.

### Error Categories

| Category | Handling |
|----------|----------|
| Authentication failure | Abort immediately with clear message |
| Network timeout | Retry 3 times with exponential backoff |
| Rate limiting (429) | Wait per `Retry-After` header, then retry |
| Page not found (404) | Log warning, skip page |
| Permission denied (403) | Log warning, skip page |
| Conversion error | Log error, skip page |
| Upload conflict | Skip page (per conflict resolution setting) |
| File too large | Log warning, skip attachment |

### Logging Format

```
2024-01-15 10:30:45 [INFO] Starting export of space TEAM
2024-01-15 10:30:46 [INFO] Exporting page: "Getting Started" (id: 12345)
2024-01-15 10:30:47 [WARNING] Skipping attachment >100MB: large-video.mp4
2024-01-15 10:30:48 [ERROR] Failed to export page 67890: 403 Forbidden
2024-01-15 10:31:00 [INFO] Export complete: 45 pages, 3 skipped, 1 failed
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success (all pages migrated) |
| 1 | Partial success (some pages failed) |
| 2 | Complete failure (no pages migrated) |
| 3 | Configuration error |
| 4 | Authentication error |

## 9. Supported File Types

### Attachments

| Category | Extensions |
|----------|------------|
| Images | `.png`, `.jpg`, `.jpeg`, `.gif`, `.svg`, `.webp` |
| Documents | `.pdf`, `.doc`, `.docx`, `.xls`, `.xlsx`, `.ppt`, `.pptx` |
| Text | `.txt`, `.csv`, `.json`, `.xml` |
| Archives | `.zip`, `.tar`, `.gz` |

### Size Limits

- Maximum file size: **100 MB** per attachment
- Files exceeding limit are logged and skipped

## 10. Limitations & Constraints

### Content Limitations
- Confluence macros with dynamic content (Jira, charts) are removed or converted to placeholder text
- Page comments are not migrated
- Page version history is not preserved
- Confluence-specific permissions are not migrated
- Inline comments and annotations are not preserved

### Technical Limitations
- Requires Python 3.10 or higher
- Single-threaded operation (no parallel downloads)
- Memory usage scales with largest page/attachment size
- Confluence Cloud only (not Server/Data Center)

### Naming Constraints
- Page titles with special characters (`/`, `\`, `:`, `*`, `?`, `"`, `<`, `>`, `|`) are sanitized
- Duplicate page titles within same parent get numeric suffix

## 11. Success Metrics

A successful migration is measured by:

| Metric | Target |
|--------|--------|
| Pages migrated | >95% of source pages |
| Attachments migrated | >90% of attachments under size limit |
| Content fidelity | Text content preserved, formatting close |
| Links preserved | Internal links rewritten correctly |
| Resume capability | Can continue after interruption |

### Verification Checklist
- [ ] Page hierarchy matches source structure
- [ ] All text content is readable
- [ ] Images display correctly in Collectives
- [ ] Internal links navigate to correct pages
- [ ] State file reflects accurate migration status
