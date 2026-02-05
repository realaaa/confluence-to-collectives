# Confluence to Nextcloud Collectives Migration Tool

A Python CLI tool for migrating content from Confluence Cloud to Nextcloud Collectives. Exports pages and attachments, converts HTML to Markdown, and uploads to Collectives via WebDAV while preserving page hierarchy.

## Features

- **Full migration pipeline** - Export, convert, and upload in one command
- **Selective migration** - Migrate specific pages, spaces, or everything
- **Preserves hierarchy** - Maintains parent-child page structure
- **Attachment support** - Migrates images and documents
- **Resumable** - State file tracks progress for interrupted migrations
- **Dry-run mode** - Preview changes before execution
- **Skip conflicts** - Existing pages are not overwritten
- **Flexible configuration** - Config file, environment variables, or CLI options

## Prerequisites

- Python 3.10 or higher
- Confluence Cloud account with API access
- Nextcloud instance with Collectives app installed
- Network access to both services

## Installation

```bash
# Clone the repository
git clone https://github.com/yourorg/confluence-to-collectives.git
cd confluence-to-collectives

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt
```

### Dependencies

```
requests
webdavclient3
markdownify
pyyaml
click
rich
```

## Configuration

### 1. Create Configuration File

Copy the example config and edit with your settings:

```bash
cp config.example.yaml config.yaml
```

```yaml
# config.yaml
confluence:
  base_url: "https://yourcompany.atlassian.net"
  username: "your-email@example.com"
  api_token: "${CONFLUENCE_API_TOKEN}"

nextcloud:
  server: "https://cloud.example.com"
  username: "your-username"
  password: "${NEXTCLOUD_PASSWORD}"
  collective: "Your Collective Name"

migration:
  output_dir: "./migration-data"
  max_file_size_mb: 100
  include_metadata: true
  target_parent: "MigratedPages"

logging:
  level: "INFO"
  file: "./migration.log"
```

### 2. Create Confluence API Token

1. Go to [Atlassian Account Settings](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token**
3. Enter a label (e.g., "Collectives Migration")
4. Copy the generated token
5. Set as environment variable:

```bash
export CONFLUENCE_API_TOKEN="your-api-token-here"
```

### 3. Create Nextcloud App Password (Recommended)

Using an app password is more secure than your main password:

1. Log into Nextcloud
2. Go to **Settings** → **Security**
3. Under "Devices & sessions", enter a name (e.g., "Migration Tool")
4. Click **Create new app password**
5. Copy the generated password
6. Set as environment variable:

```bash
export NEXTCLOUD_PASSWORD="your-app-password-here"
```

### 4. Verify Collective Access

Ensure your Nextcloud user has write access to the target collective. The collective must already exist in Nextcloud.

## Usage

### Full Migration (Recommended)

Migrate an entire space in one command:

```bash
python migrate.py migrate --space-id TEAM --config config.yaml
```

### Step-by-Step Migration

For more control, run each phase separately:

```bash
# Step 1: Export from Confluence
python migrate.py export --space-id TEAM

# Step 2: Convert HTML to Markdown
python migrate.py convert

# Step 3: Upload to Collectives
python migrate.py upload
```

### Preview with Dry Run

See what would be migrated without making changes:

```bash
python migrate.py migrate --space-id TEAM --dry-run
```

### Common Examples

```bash
# Migrate a specific space
python migrate.py migrate --space-id DOCS

# Migrate specific pages by ID
python migrate.py migrate --page-ids 12345,67890

# Migrate everything accessible
python migrate.py migrate --all

# Migrate without images (text only)
python migrate.py migrate --space-id TEAM --exclude-images

# Migrate without any attachments
python migrate.py migrate --space-id TEAM --exclude-attachments

# Custom target location in Collectives
python migrate.py migrate --space-id TEAM --target-parent "Archived/Confluence"

# Verbose output for debugging
python migrate.py migrate --space-id TEAM --debug

# Custom log file location
python migrate.py migrate --space-id TEAM --log-file /var/log/migration.log
```

## CLI Reference

### Commands

| Command | Description |
|---------|-------------|
| `export` | Export pages from Confluence to local HTML |
| `convert` | Convert local HTML files to Markdown |
| `upload` | Upload converted Markdown to Collectives |
| `migrate` | Run full pipeline (export → convert → upload) |

### Global Options

| Option | Short | Description |
|--------|-------|-------------|
| `--help` | `-h` | Show help message and exit |
| `--config <path>` | `-c` | Path to configuration file |
| `--dry-run` | | Preview changes without executing |
| `--debug` | `-v` | Enable verbose/debug logging |
| `--log-file <path>` | | Custom log file path |

### Source Selection Options (export/migrate)

| Option | Description |
|--------|-------------|
| `--space-id <id>` | Migrate a specific Confluence space |
| `--page-ids <ids>` | Migrate specific pages (comma-separated IDs) |
| `--all` | Migrate all accessible content |

### Content Options (export/migrate)

| Option | Description |
|--------|-------------|
| `--exclude-images` | Skip image attachments |
| `--exclude-attachments` | Skip all attachments |

### Target Options (upload/migrate)

| Option | Description | Default |
|--------|-------------|---------|
| `--target-parent <name>` | Parent page in Collectives | `MigratedPages` |

## How It Works

### Export Process

The tool connects to Confluence Cloud using the REST API:

1. **Authentication**: Uses HTTP Basic Auth with your email and API token
2. **Page discovery**: Fetches page tree from `/wiki/api/v2/pages`
3. **Content export**: Downloads HTML from `/wiki/rest/api/content/{id}?expand=body.export_view`
4. **Attachments**: Downloads files from `/wiki/rest/api/content/{id}/child/attachment`
5. **Local storage**: Saves to `./migration-data/export/`

### Conversion Process

Converts Confluence HTML to Collectives-compatible Markdown:

1. **HTML parsing**: Reads exported HTML content
2. **Markdown conversion**: Uses `markdownify` library
3. **Macro handling**: Converts or removes Confluence-specific macros
4. **Image rewriting**: Updates image paths to Collectives format
5. **Metadata injection**: Adds migration info to page header (optional)
6. **Output**: Creates Markdown files in `./migration-data/converted/`

### Upload Process

Uploads content to Nextcloud via WebDAV:

1. **WebDAV connection**: Connects to `https://{server}/remote.php/dav/files/{user}/`
2. **Directory creation**: Creates collective folder structure
3. **Page upload**: Creates `.md` files for each page
4. **Attachment upload**: Places files in `.attachments.{id}/` directories
5. **State tracking**: Records progress in state file

### Directory Structure

After migration, your Collectives will have:

```
Your Collective/
└── MigratedPages/
    ├── Page Title.md
    ├── Parent Page/
    │   ├── Readme.md          # Parent page content
    │   ├── Child Page.md
    │   └── .attachments.123/
    │       └── diagram.png
    └── Another Page.md
```

## State & Resume

The tool maintains a state file (`.migration-state.json`) tracking each page's status:

- `pending` - Not yet processed
- `exported` - Downloaded from Confluence
- `converted` - Converted to Markdown
- `uploaded` - Successfully uploaded to Collectives
- `failed` - Error occurred (will retry on resume)

### Resuming an Interrupted Migration

Simply run the same command again:

```bash
python migrate.py migrate --space-id TEAM
```

The tool automatically:
- Loads existing state file
- Skips pages already at target status
- Retries failed pages
- Continues from where it stopped

### Resetting Migration State

To start fresh, delete the state file:

```bash
rm .migration-state.json
```

## Troubleshooting

### Authentication Errors

**Confluence 401 Unauthorized**
- Verify your API token is correct
- Ensure you're using your email as username, not your display name
- Check the token hasn't expired

**Nextcloud 401 Unauthorized**
- Verify your app password is correct
- Ensure the user has access to the target collective
- Try with your main password to rule out app password issues

### Permission Errors

**Confluence 403 Forbidden on some pages**
- You don't have access to those pages in Confluence
- Pages are logged and skipped automatically

**Nextcloud 403 Forbidden**
- Your user may not have write access to the collective
- Ask the collective admin to grant you edit permissions

### Content Issues

**Missing images after migration**
- Check if `--exclude-images` was used
- Verify attachments were under 100MB limit
- Check the migration log for attachment errors

**Broken internal links**
- Links to non-migrated pages won't work
- Consider migrating the entire space for complete link preservation

**Garbled formatting**
- Some complex Confluence macros don't convert cleanly
- Review affected pages and manually fix if needed

### Performance Issues

**Migration is slow**
- This is single-threaded by design for reliability
- Large spaces with many attachments take time
- Use `--exclude-attachments` for faster text-only migration

**Out of memory**
- Usually caused by very large attachments
- Use `--exclude-attachments` or reduce `max_file_size_mb` in config

### Common Error Messages

| Error | Cause | Solution |
|-------|-------|----------|
| `Connection refused` | Network issue | Check URLs and network connectivity |
| `SSL certificate verify failed` | Certificate issue | Verify server certificates |
| `Collective not found` | Wrong collective name | Check exact spelling in Nextcloud |
| `Rate limit exceeded` | Too many API requests | Tool auto-retries; wait if persists |

### Debug Mode

For detailed troubleshooting, enable debug logging:

```bash
python migrate.py migrate --space-id TEAM --debug --log-file debug.log
```

Review `debug.log` for detailed request/response information.

## Limitations

- **Confluence Cloud only** - Does not support Confluence Server/Data Center
- **No comments** - Page comments are not migrated
- **No history** - Only the current page version is migrated
- **No permissions** - Confluence access controls are not transferred
- **Dynamic macros** - Jira issues, charts, etc. become placeholder text
- **100MB file limit** - Larger attachments are skipped
- **Special characters** - Page titles are sanitized (some characters removed)

## License

MIT License - See LICENSE file for details.
