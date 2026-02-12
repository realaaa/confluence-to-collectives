# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Plan Mode

- First use Plan Mode to create a PRD.md specification document
- Make the plan extremely concise. Sacrifice grammar for the sake of concision.
- At the end of each plan, give me a list of unresolved questions to answer, if any.
- In addition to the provided scope and requirements here - use AskUserQuestion tool, to refine the specification with user.

## Project Overview and Requirements

* Project goal is to create a CLI tool to migrate pages and attachments from Confluence Cloud to Nextcloud Collectives. Single-file Python application (`migrate.py`) using Click, three-phase pipeline: export → convert → upload. See `PRD.md` for full specification.
* Script should have support for standard command line options - including help, dry-run, debug logging, exclude images, exclude attachments, and specify non standard output migration log file name.
* Script should have support for scope command line options - specific pages IDs (separated by commas if more than one), specific space ID, or everything accessible in the source Confluence.
* Script should also allow to specify target parent page in Collectives, under which it will create new migrated pages (default to be MigratedPages).
* Images within the pages are to be correctly referenced in Markdown documents, so that they are correctly visible in resulting Collectives pages.
* All standard images are to be supported, as well as PDFs and standard Office documents attachments.
* Tables from Confluence to be supported also - by converting them to Markdown compatible format.
* Comments from Confluence to be appended to the Markdown page - with H1 heading who and when posted the comment to the Confluence page. 

## Project documentation

* Project to have a industry standard README.md for end users on how to use the tool, including how to create required Confluence and Collectives API / access tokens.
* README.md to outline high-level logic of how this tool works, what methods it is using on both source and target sides, and any caveats / limitations / requirements.

## Commands

```bash
# Setup
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run migrations
python migrate.py migrate --space SPACE_KEY        # Full pipeline
python migrate.py export --space SPACE_KEY         # Export only
python migrate.py convert                          # Convert HTML to Markdown
python migrate.py upload                           # Upload to Nextcloud
python migrate.py status                           # Show migration progress

# Testing
python -m pytest tests/ -v
python -m pytest tests/test_converter.py -k "test_macro"  # Specific test
```

## Architecture

**migrate.py** — single file, all logic:

- **ConfluenceClient**: REST API v2 client. Uses `body-format=export_view` for clean HTML. Cursor-based pagination. Rate limiting with exponential backoff.
- **Converter**: BeautifulSoup for Confluence-specific elements → html2text for Markdown. Rewrites image URLs to relative paths. Unsupported macros → HTML comments.
- **NextcloudClient**: WebDAV via requests. MKCOL for directories, PUT for files. Target path: `remote.php/dav/files/{user}/Collectives/{collective}/`.
- **State**: JSON persistence in `.migration-state.json`. Per-page status: pending → exported → converted → uploaded | failed.

**Key rules:**
- Pages with children → folder + `Readme.md`. Leaf pages → `{title}.md`.
- Attachments sit alongside their page's Markdown file (same directory).
- Image refs in Markdown must be relative paths (e.g., `![alt](image.png)`).
- Space homepage is always the root `Readme.md` of the space folder.
- Filenames sanitised: strip `/ \ : * ? " < > |`, cap 200 chars, dedupe with `-2` suffix.

## Tech Stack

- **Language:** Python 3.11+
- **Config:** Environment variables (.env)

## Configuration

Credentials via `.env` (auto-loaded via python-dotenv):
- `CONFLUENCE_BASE_URL`, `CONFLUENCE_USERNAME`, `CONFLUENCE_API_TOKEN`
- `NEXTCLOUD_HOST` (no trailing slash), `NEXTCLOUD_USERNAME`, `NEXTCLOUD_PASSWORD`, `NEXTCLOUD_COLLECTIVE`

Optional `config.yaml` with `${ENV_VAR}` substitution for non-secret settings.

## MCP Development Setup

Two MCP servers are configured in `.mcp.json` for **development and testing only** — the final tool itself has zero MCP dependency.

- **`sooperset/mcp-atlassian`** : to query real Confluence pages, inspect hierarchies, fetch export_view HTML for fixture generation.
- **`cbcoutinho/nextcloud-mcp-server`** : to verify WebDAV uploads, check directory structures, test Collectives rendering. This server will be run locally without docker in this project.

Use these to generate realistic test fixtures and validate API assumptions against live systems. Never add MCP imports to `migrate.py`.

## Exit Codes

- 0: Success
- 1: Partial failure (some pages failed)
- 2: Complete failure
- 3: Config error
- 4: Auth error

## Known Gotchas

- **Confluence download links need `/wiki` prefix**: API returns relative paths like `/download/attachments/...` — must prepend `/wiki` to form working URLs on Confluence Cloud.
- **Confluence auth failure is silent**: Invalid API tokens still return 200 but resolve as "Anonymous". Public spaces still appear, masking the issue. Verify with `/wiki/rest/api/user/current` — if `type` is `anonymous`, the token is bad.
- **html2text breaks tables with block elements**: Confluence puts `<h3>` inside `<th>` cells. These must be replaced with inline elements (e.g., `<strong>`) in `preprocess_html()` before conversion, or tables get destroyed.
- **Strip `plugin_attachments_container`**: Confluence export_view includes the full attachment management UI HTML. Must `decompose()` it in preprocessing — the tool generates its own clean attachment section.
- **`NEXTCLOUD_HOST` must not have trailing slash**: The Nextcloud MCP server (`nextcloud-mcp-server`) validates this and rejects URLs like `https://example.com/` — use `https://example.com` instead.

## Don't

- Don't log credentials, even at DEBUG level — redact all tokens and passwords.
- Don't add external binary dependencies (no Pandoc, no headless browsers).
- Don't couple `migrate.py` to MCP servers — direct `requests` calls only.
- Don't abort the full migration on a single page failure — track it and continue.
- Don't commit `.mcp.json` — it contains credentials and is in `.gitignore`.
