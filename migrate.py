#!/usr/bin/env python3
"""
Confluence to Nextcloud Collectives Migration Tool

A CLI tool to migrate content from Confluence Cloud to Nextcloud Collectives.
Supports incremental migration with resume capability.
"""

import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, quote

import click
import markdownify
import requests
import yaml
from dotenv import load_dotenv
from rich.console import Console

# Load environment variables from .env file
load_dotenv()
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from webdav3.client import Client as WebDAVClient
from webdav3.exceptions import WebDavException

# Exit codes
EXIT_SUCCESS = 0
EXIT_PARTIAL_FAILURE = 1
EXIT_COMPLETE_FAILURE = 2
EXIT_CONFIG_ERROR = 3
EXIT_AUTH_ERROR = 4

# Constants
STATE_FILE = ".migration-state.json"
MAX_RETRIES = 3
RETRY_BACKOFF = 2  # seconds, multiplied by attempt number

console = Console()
logger = logging.getLogger(__name__)


# =============================================================================
# Configuration Classes
# =============================================================================

@dataclass
class ConfluenceConfig:
    """Confluence Cloud configuration."""
    base_url: str
    username: str
    api_token: str


@dataclass
class NextcloudConfig:
    """Nextcloud configuration."""
    server: str
    username: str
    password: str
    collective: str


@dataclass
class MigrationConfig:
    """Migration behavior configuration."""
    output_dir: str = "./migration-data"
    max_file_size_mb: int = 100
    include_metadata: bool = True
    target_parent: str = "MigratedPages"


@dataclass
class Config:
    """Complete configuration."""
    confluence: ConfluenceConfig
    nextcloud: NextcloudConfig
    migration: MigrationConfig = field(default_factory=MigrationConfig)


def substitute_env_vars(value: str) -> str:
    """Substitute ${VAR} patterns with environment variables."""
    if not isinstance(value, str):
        return value

    pattern = r'\$\{([^}]+)\}'

    def replacer(match):
        var_name = match.group(1)
        env_value = os.environ.get(var_name)
        if env_value is None:
            logger.warning(f"Environment variable {var_name} not set")
            return match.group(0)  # Return original if not set
        return env_value

    return re.sub(pattern, replacer, value)


def process_config_dict(d: dict) -> dict:
    """Recursively process config dict to substitute env vars."""
    result = {}
    for key, value in d.items():
        if isinstance(value, dict):
            result[key] = process_config_dict(value)
        elif isinstance(value, str):
            result[key] = substitute_env_vars(value)
        else:
            result[key] = value
    return result


def load_config(config_path: str) -> Config:
    """Load configuration from YAML file with env var substitution."""
    try:
        with open(config_path, 'r') as f:
            raw_config = yaml.safe_load(f)
    except FileNotFoundError:
        raise click.ClickException(f"Config file not found: {config_path}")
    except yaml.YAMLError as e:
        raise click.ClickException(f"Invalid YAML in config file: {e}")

    config_dict = process_config_dict(raw_config)

    try:
        confluence = ConfluenceConfig(
            base_url=config_dict['confluence']['base_url'].rstrip('/'),
            username=config_dict['confluence']['username'],
            api_token=config_dict['confluence']['api_token'],
        )
        nextcloud = NextcloudConfig(
            server=config_dict['nextcloud']['server'].rstrip('/'),
            username=config_dict['nextcloud']['username'],
            password=config_dict['nextcloud']['password'],
            collective=config_dict['nextcloud']['collective'],
        )
        migration_dict = config_dict.get('migration', {})
        migration = MigrationConfig(
            output_dir=migration_dict.get('output_dir', './migration-data'),
            max_file_size_mb=migration_dict.get('max_file_size_mb', 100),
            include_metadata=migration_dict.get('include_metadata', True),
            target_parent=migration_dict.get('target_parent', 'MigratedPages'),
        )
        return Config(confluence=confluence, nextcloud=nextcloud, migration=migration)
    except KeyError as e:
        raise click.ClickException(f"Missing required config key: {e}")


# =============================================================================
# State Management
# =============================================================================

@dataclass
class PageState:
    """State for a single page."""
    page_id: str
    title: str
    status: str = "pending"  # pending, exported, converted, uploaded, failed
    error: Optional[str] = None
    parent_id: Optional[str] = None
    path: Optional[str] = None  # Path in output directory


@dataclass
class MigrationState:
    """Complete migration state."""
    pages: dict = field(default_factory=dict)  # page_id -> PageState
    tree: dict = field(default_factory=dict)  # Hierarchy information
    space_id: Optional[str] = None
    last_updated: Optional[str] = None


def load_state(output_dir: Path) -> MigrationState:
    """Load migration state from file."""
    state_path = output_dir / STATE_FILE
    if state_path.exists():
        try:
            with open(state_path, 'r') as f:
                data = json.load(f)
            state = MigrationState(
                pages={pid: PageState(**ps) for pid, ps in data.get('pages', {}).items()},
                tree=data.get('tree', {}),
                space_id=data.get('space_id'),
                last_updated=data.get('last_updated'),
            )
            logger.info(f"Loaded state with {len(state.pages)} pages")
            return state
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Failed to load state file, starting fresh: {e}")
    return MigrationState()


def save_state(state: MigrationState, output_dir: Path):
    """Save migration state to file."""
    state_path = output_dir / STATE_FILE
    state.last_updated = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    data = {
        'pages': {pid: asdict(ps) for pid, ps in state.pages.items()},
        'tree': state.tree,
        'space_id': state.space_id,
        'last_updated': state.last_updated,
    }
    with open(state_path, 'w') as f:
        json.dump(data, f, indent=2)


# =============================================================================
# Confluence Client
# =============================================================================

class ConfluenceClient:
    """Client for Confluence Cloud REST API."""

    def __init__(self, config: ConfluenceConfig):
        self.config = config
        self.session = requests.Session()
        self.session.auth = (config.username, config.api_token)
        self.session.headers.update({
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        })

    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """Make an API request with retry logic."""
        url = urljoin(self.config.base_url, endpoint)

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self.session.request(method, url, **kwargs)

                if response.status_code == 401:
                    raise click.ClickException("Authentication failed. Check your Confluence credentials.")

                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', RETRY_BACKOFF * attempt))
                    logger.warning(f"Rate limited, waiting {retry_after}s")
                    time.sleep(retry_after)
                    continue

                response.raise_for_status()
                return response

            except requests.exceptions.RequestException as e:
                if attempt == MAX_RETRIES:
                    raise
                wait_time = RETRY_BACKOFF * attempt
                logger.warning(f"Request failed (attempt {attempt}/{MAX_RETRIES}), retrying in {wait_time}s: {e}")
                time.sleep(wait_time)

        raise click.ClickException("Max retries exceeded")

    def get_spaces(self) -> list[dict]:
        """Get all accessible spaces."""
        spaces = []
        cursor = None

        while True:
            params = {'limit': 100}
            if cursor:
                params['cursor'] = cursor

            response = self._request('GET', '/wiki/api/v2/spaces', params=params)
            data = response.json()

            spaces.extend(data.get('results', []))

            # Check for pagination
            links = data.get('_links', {})
            if 'next' not in links:
                break
            # Extract cursor from next link
            next_link = links['next']
            cursor_match = re.search(r'cursor=([^&]+)', next_link)
            if cursor_match:
                cursor = cursor_match.group(1)
            else:
                break

        return spaces

    def get_space_by_key(self, space_key: str) -> dict:
        """Get a space by its key (e.g., 'WGS')."""
        response = self._request('GET', '/wiki/api/v2/spaces', params={'keys': space_key})
        data = response.json()
        results = data.get('results', [])
        if not results:
            raise click.ClickException(f"Space with key '{space_key}' not found")
        return results[0]

    def resolve_space_id(self, space: str) -> str:
        """Resolve a space key or ID to a numeric ID."""
        # If it's already numeric, return as-is
        if space.isdigit():
            return space
        # Otherwise, look up by key
        space_data = self.get_space_by_key(space)
        space_id = space_data.get('id')
        space_name = space_data.get('name', space)
        logger.info(f"Resolved space key '{space}' to ID {space_id} ({space_name})")
        return space_id

    def get_pages(self, space_id: str = None, page_ids: list[str] = None) -> list[dict]:
        """Get pages from a space or by specific IDs."""
        pages = []

        if page_ids:
            # Fetch specific pages
            for page_id in page_ids:
                try:
                    response = self._request('GET', f'/wiki/api/v2/pages/{page_id}')
                    pages.append(response.json())
                except requests.exceptions.HTTPError as e:
                    logger.error(f"Failed to fetch page {page_id}: {e}")
        elif space_id:
            # Fetch all pages in space
            cursor = None
            while True:
                params = {'space-id': space_id, 'limit': 100, 'status': 'current'}
                if cursor:
                    params['cursor'] = cursor

                response = self._request('GET', '/wiki/api/v2/pages', params=params)
                data = response.json()

                pages.extend(data.get('results', []))

                links = data.get('_links', {})
                if 'next' not in links:
                    break
                next_link = links['next']
                cursor_match = re.search(r'cursor=([^&]+)', next_link)
                if cursor_match:
                    cursor = cursor_match.group(1)
                else:
                    break

        return pages

    def get_page_content(self, page_id: str) -> tuple[str, dict]:
        """Get page HTML content and metadata."""
        # Use v1 API for expanded content
        response = self._request(
            'GET',
            f'/wiki/rest/api/content/{page_id}',
            params={'expand': 'body.export_view,ancestors,version,space'}
        )
        data = response.json()

        html = data.get('body', {}).get('export_view', {}).get('value', '')
        metadata = {
            'id': data.get('id'),
            'title': data.get('title'),
            'version': data.get('version', {}).get('number'),
            'space': data.get('space', {}).get('key'),
            'ancestors': [a.get('id') for a in data.get('ancestors', [])],
            'url': data.get('_links', {}).get('webui', ''),
        }

        return html, metadata

    def get_page_children(self, page_id: str) -> list[dict]:
        """Get child pages of a page."""
        children = []
        cursor = None

        while True:
            params = {'limit': 100}
            if cursor:
                params['cursor'] = cursor

            response = self._request('GET', f'/wiki/api/v2/pages/{page_id}/children', params=params)
            data = response.json()

            children.extend(data.get('results', []))

            links = data.get('_links', {})
            if 'next' not in links:
                break
            next_link = links['next']
            cursor_match = re.search(r'cursor=([^&]+)', next_link)
            if cursor_match:
                cursor = cursor_match.group(1)
            else:
                break

        return children

    def get_attachments(self, page_id: str) -> list[dict]:
        """Get attachments for a page."""
        response = self._request(
            'GET',
            f'/wiki/rest/api/content/{page_id}/child/attachment',
            params={'expand': 'version'}
        )
        data = response.json()
        return data.get('results', [])

    def download_attachment(self, page_id: str, attachment: dict, dest: Path):
        """Download an attachment to a local file."""
        download_link = attachment.get('_links', {}).get('download', '')
        if not download_link:
            raise ValueError(f"No download link for attachment {attachment.get('title')}")

        url = urljoin(self.config.base_url, '/wiki' + download_link)

        response = self.session.get(url, stream=True)
        response.raise_for_status()

        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)


# =============================================================================
# HTML to Markdown Converter
# =============================================================================

def handle_confluence_macros(html: str) -> str:
    """Pre-process Confluence macros before markdown conversion."""

    # Convert info/warning/note/tip panels to blockquotes
    # Pattern: <ac:structured-macro ac:name="info|warning|note|tip">...<ac:rich-text-body>content</ac:rich-text-body>...</ac:structured-macro>
    panel_pattern = r'<ac:structured-macro[^>]*ac:name="(info|warning|note|tip)"[^>]*>.*?<ac:rich-text-body>(.*?)</ac:rich-text-body>.*?</ac:structured-macro>'

    def panel_replacer(match):
        panel_type = match.group(1).upper()
        content = match.group(2)
        return f'<blockquote><strong>{panel_type}:</strong> {content}</blockquote>'

    html = re.sub(panel_pattern, panel_replacer, html, flags=re.DOTALL | re.IGNORECASE)

    # Remove TOC macros
    html = re.sub(r'<ac:structured-macro[^>]*ac:name="toc"[^>]*>.*?</ac:structured-macro>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<ac:structured-macro[^>]*ac:name="toc"[^>]*/>', '', html, flags=re.IGNORECASE)

    # Convert code/noformat macros to pre tags
    code_pattern = r'<ac:structured-macro[^>]*ac:name="(code|noformat)"[^>]*>.*?<ac:plain-text-body><!\[CDATA\[(.*?)\]\]></ac:plain-text-body>.*?</ac:structured-macro>'

    def code_replacer(match):
        code = match.group(2)
        return f'<pre><code>{code}</code></pre>'

    html = re.sub(code_pattern, code_replacer, html, flags=re.DOTALL | re.IGNORECASE)

    # Strip unsupported macros (Jira, charts, etc.) but keep any text content
    html = re.sub(r'<ac:structured-macro[^>]*ac:name="jira"[^>]*>.*?</ac:structured-macro>', '[JIRA link removed]', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<ac:structured-macro[^>]*ac:name="chart"[^>]*>.*?</ac:structured-macro>', '[Chart removed]', html, flags=re.DOTALL | re.IGNORECASE)

    # Remove remaining unhandled macros
    html = re.sub(r'<ac:structured-macro[^>]*>.*?</ac:structured-macro>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<ac:[^>]*>[^<]*</ac:[^>]*>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<ac:[^>]*/>', '', html, flags=re.IGNORECASE)

    # Clean up Confluence-specific elements
    html = re.sub(r'<ri:[^>]*>[^<]*</ri:[^>]*>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<ri:[^>]*/>', '', html, flags=re.IGNORECASE)

    return html


def rewrite_image_references(md: str, page_id: str) -> str:
    """Rewrite image paths to point to local attachments directory."""
    # Pattern for markdown images
    img_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'

    def img_replacer(match):
        alt_text = match.group(1)
        src = match.group(2)

        # If it's a Confluence attachment URL, rewrite to local path
        if '/download/attachments/' in src or '/rest/api/content/' in src:
            # Extract filename from URL
            filename = src.split('/')[-1].split('?')[0]
            filename = requests.utils.unquote(filename)
            return f'![{alt_text}](.attachments.{page_id}/{filename})'

        return match.group(0)

    return re.sub(img_pattern, img_replacer, md)


def convert_html_to_markdown(html: str, page_id: str) -> str:
    """Convert Confluence HTML to Markdown."""
    # Pre-process macros
    html = handle_confluence_macros(html)

    # Convert using markdownify
    md = markdownify.markdownify(
        html,
        heading_style="ATX",
        bullets="-",
        code_language="",
    )

    # Rewrite image paths
    md = rewrite_image_references(md, page_id)

    # Clean up excessive whitespace
    md = re.sub(r'\n{3,}', '\n\n', md)
    md = md.strip()

    return md


# =============================================================================
# Nextcloud Client
# =============================================================================

class NextcloudClient:
    """Client for Nextcloud WebDAV and Collectives."""

    def __init__(self, config: NextcloudConfig):
        self.config = config
        self.webdav_base = f"{config.server}/remote.php/dav/files/{config.username}/"
        self.collectives_path = f"Collectives/{config.collective}"

        self.client = WebDAVClient({
            'webdav_hostname': f"{config.server}/remote.php/dav/files/{config.username}/",
            'webdav_login': config.username,
            'webdav_password': config.password,
        })

    def ensure_directory(self, path: str):
        """Ensure a directory exists, creating parent directories as needed."""
        full_path = f"{self.collectives_path}/{path}"
        parts = full_path.split('/')

        current = ""
        for part in parts:
            if not part:
                continue
            current = f"{current}/{part}" if current else part
            try:
                if not self.client.check(current):
                    self.client.mkdir(current)
                    logger.debug(f"Created directory: {current}")
            except WebDavException as e:
                # Directory might already exist
                logger.debug(f"Directory check/create for {current}: {e}")

    def upload_file(self, local_path: Path, remote_path: str):
        """Upload a file to Nextcloud."""
        full_path = f"{self.collectives_path}/{remote_path}"

        # Ensure parent directory exists
        parent = '/'.join(full_path.split('/')[:-1])
        if parent:
            self.ensure_directory('/'.join(remote_path.split('/')[:-1]))

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self.client.upload_sync(
                    remote_path=full_path,
                    local_path=str(local_path)
                )
                logger.debug(f"Uploaded: {local_path} -> {full_path}")
                return
            except WebDavException as e:
                if attempt == MAX_RETRIES:
                    raise
                wait_time = RETRY_BACKOFF * attempt
                logger.warning(f"Upload failed (attempt {attempt}/{MAX_RETRIES}), retrying in {wait_time}s: {e}")
                time.sleep(wait_time)

    def file_exists(self, remote_path: str) -> bool:
        """Check if a file exists on Nextcloud."""
        full_path = f"{self.collectives_path}/{remote_path}"
        try:
            return self.client.check(full_path)
        except WebDavException:
            return False


# =============================================================================
# Utilities
# =============================================================================

def sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filename."""
    # Remove or replace problematic characters
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', name)
    # Collapse multiple underscores
    sanitized = re.sub(r'_+', '_', sanitized)
    # Remove leading/trailing whitespace and dots
    sanitized = sanitized.strip('. ')
    # Limit length
    if len(sanitized) > 200:
        sanitized = sanitized[:200]
    return sanitized or "untitled"


def setup_logging(debug: bool, log_file: Optional[str]):
    """Configure logging with Rich handler."""
    level = logging.DEBUG if debug else logging.INFO

    handlers = [
        RichHandler(
            console=console,
            show_time=True,
            show_path=debug,
            rich_tracebacks=True,
        )
    ]

    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        handlers=handlers,
        format="%(message)s",
    )


# =============================================================================
# CLI Commands
# =============================================================================

class Context:
    """CLI context object."""
    def __init__(self):
        self.config: Optional[Config] = None
        self.dry_run: bool = False
        self.state: Optional[MigrationState] = None
        self.output_dir: Optional[Path] = None


pass_context = click.make_pass_decorator(Context, ensure=True)


@click.group()
@click.option('--config', '-c', 'config_path', type=click.Path(exists=True),
              default='config.yaml', help='Path to configuration file')
@click.option('--env-file', '-e', 'env_file', type=click.Path(exists=True),
              help='Path to .env file (default: .env in current directory)')
@click.option('--dry-run', is_flag=True, help='Show what would be done without making changes')
@click.option('--debug', '-v', is_flag=True, help='Enable debug logging')
@click.option('--log-file', type=click.Path(), help='Write logs to file')
@click.pass_context
def cli(ctx, config_path, env_file, dry_run, debug, log_file):
    """Confluence to Nextcloud Collectives migration tool."""
    # Load custom .env file if specified
    if env_file:
        load_dotenv(env_file, override=True)

    setup_logging(debug, log_file)

    ctx.ensure_object(Context)
    ctx.obj.dry_run = dry_run

    try:
        ctx.obj.config = load_config(config_path)
    except click.ClickException:
        raise
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        sys.exit(EXIT_CONFIG_ERROR)

    ctx.obj.output_dir = Path(ctx.obj.config.migration.output_dir)
    ctx.obj.output_dir.mkdir(parents=True, exist_ok=True)
    ctx.obj.state = load_state(ctx.obj.output_dir)


@cli.command()
@click.option('--space-id', help='Confluence space key (e.g., WGS) or numeric ID')
@click.option('--page-ids', help='Comma-separated list of page IDs to export')
@click.option('--all', 'all_content', is_flag=True, help='Export all accessible content')
@click.option('--exclude-images', is_flag=True, help='Skip image attachments')
@click.option('--exclude-attachments', is_flag=True, help='Skip all attachments')
@pass_context
def export(ctx, space_id, page_ids, all_content, exclude_images, exclude_attachments):
    """Export content from Confluence to local HTML files."""
    config = ctx.config
    state = ctx.state
    dry_run = ctx.dry_run
    output_dir = ctx.output_dir

    if not any([space_id, page_ids, all_content]):
        raise click.ClickException("Must specify --space-id, --page-ids, or --all")

    confluence = ConfluenceClient(config.confluence)
    export_dir = output_dir / "export" / "pages"
    export_dir.mkdir(parents=True, exist_ok=True)

    # Determine pages to export
    pages = []
    if page_ids:
        page_id_list = [p.strip() for p in page_ids.split(',')]
        pages = confluence.get_pages(page_ids=page_id_list)
    elif space_id:
        # Resolve space key (e.g., "WGS") to numeric ID if needed
        resolved_space_id = confluence.resolve_space_id(space_id)
        pages = confluence.get_pages(space_id=resolved_space_id)
        state.space_id = resolved_space_id
    elif all_content:
        spaces = confluence.get_spaces()
        for space in spaces:
            space_pages = confluence.get_pages(space_id=space['id'])
            pages.extend(space_pages)

    console.print(f"[bold]Found {len(pages)} pages to export[/bold]")

    if dry_run:
        for page in pages:
            console.print(f"  Would export: {page.get('title')} (ID: {page.get('id')})")
        return

    # Build tree structure
    tree = {}
    for page in pages:
        page_id = page.get('id')
        parent_id = page.get('parentId')
        tree[page_id] = {
            'title': page.get('title'),
            'parent_id': parent_id,
            'children': [],
        }

    # Link children
    for page_id, node in tree.items():
        parent_id = node['parent_id']
        if parent_id and parent_id in tree:
            tree[parent_id]['children'].append(page_id)

    state.tree = tree

    # Export each page
    success_count = 0
    fail_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Exporting pages...", total=len(pages))

        for page in pages:
            page_id = page.get('id')
            title = page.get('title', 'Untitled')

            # Check if already exported
            if page_id in state.pages and state.pages[page_id].status in ('exported', 'converted', 'uploaded'):
                logger.info(f"Skipping already exported: {title}")
                progress.advance(task)
                continue

            progress.update(task, description=f"Exporting: {title[:40]}...")

            try:
                # Get page content
                html, metadata = confluence.get_page_content(page_id)

                # Create page directory
                page_dir = export_dir / page_id
                page_dir.mkdir(parents=True, exist_ok=True)

                # Save HTML content
                with open(page_dir / "content.html", 'w', encoding='utf-8') as f:
                    f.write(html)

                # Save metadata
                if config.migration.include_metadata:
                    with open(page_dir / "metadata.json", 'w', encoding='utf-8') as f:
                        json.dump(metadata, f, indent=2)

                # Handle attachments
                if not exclude_attachments:
                    attachments = confluence.get_attachments(page_id)
                    att_dir = page_dir / "attachments"

                    for att in attachments:
                        att_title = att.get('title', 'attachment')
                        media_type = att.get('metadata', {}).get('mediaType', '')

                        # Skip images if requested
                        if exclude_images and media_type.startswith('image/'):
                            continue

                        # Check file size
                        size_bytes = att.get('extensions', {}).get('fileSize', 0)
                        size_mb = size_bytes / (1024 * 1024)
                        if size_mb > config.migration.max_file_size_mb:
                            logger.warning(f"Skipping large attachment: {att_title} ({size_mb:.1f}MB)")
                            continue

                        try:
                            dest = att_dir / sanitize_filename(att_title)
                            confluence.download_attachment(page_id, att, dest)
                        except Exception as e:
                            logger.error(f"Failed to download attachment {att_title}: {e}")

                # Update state
                state.pages[page_id] = PageState(
                    page_id=page_id,
                    title=title,
                    status='exported',
                    parent_id=page.get('parentId'),
                    path=str(page_dir),
                )
                success_count += 1

            except Exception as e:
                logger.error(f"Failed to export page {title}: {e}")
                state.pages[page_id] = PageState(
                    page_id=page_id,
                    title=title,
                    status='failed',
                    error=str(e),
                )
                fail_count += 1

            progress.advance(task)
            save_state(state, output_dir)

    # Save tree structure
    with open(output_dir / "export" / "tree.json", 'w') as f:
        json.dump(tree, f, indent=2)

    console.print(f"\n[bold green]Export complete:[/bold green] {success_count} succeeded, {fail_count} failed")

    if fail_count > 0:
        sys.exit(EXIT_PARTIAL_FAILURE)


@cli.command()
@pass_context
def convert(ctx):
    """Convert exported HTML files to Markdown."""
    config = ctx.config
    state = ctx.state
    dry_run = ctx.dry_run
    output_dir = ctx.output_dir

    export_dir = output_dir / "export" / "pages"
    converted_dir = output_dir / "converted"
    converted_dir.mkdir(parents=True, exist_ok=True)

    # Find pages to convert
    pages_to_convert = [
        ps for ps in state.pages.values()
        if ps.status == 'exported'
    ]

    if not pages_to_convert:
        console.print("[yellow]No pages to convert. Run 'export' first.[/yellow]")
        return

    console.print(f"[bold]Converting {len(pages_to_convert)} pages[/bold]")

    if dry_run:
        for ps in pages_to_convert:
            console.print(f"  Would convert: {ps.title}")
        return

    # Build path hierarchy based on tree
    def get_page_path(page_id: str, tree: dict) -> str:
        """Build the path for a page based on its ancestors."""
        path_parts = []
        current_id = page_id
        visited = set()

        while current_id and current_id in tree and current_id not in visited:
            visited.add(current_id)
            node = tree[current_id]
            path_parts.insert(0, sanitize_filename(node['title']))
            current_id = node.get('parent_id')

        return '/'.join(path_parts)

    success_count = 0
    fail_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Converting pages...", total=len(pages_to_convert))

        for ps in pages_to_convert:
            page_id = ps.page_id
            title = ps.title

            progress.update(task, description=f"Converting: {title[:40]}...")

            try:
                # Read HTML content
                html_path = export_dir / page_id / "content.html"
                if not html_path.exists():
                    raise FileNotFoundError(f"HTML file not found: {html_path}")

                with open(html_path, 'r', encoding='utf-8') as f:
                    html = f.read()

                # Convert to Markdown
                md = convert_html_to_markdown(html, page_id)

                # Determine output path
                page_path = get_page_path(page_id, state.tree)

                # For Collectives, pages with children need their content in Readme.md
                has_children = bool(state.tree.get(page_id, {}).get('children'))

                if has_children:
                    md_dir = converted_dir / page_path
                    md_dir.mkdir(parents=True, exist_ok=True)
                    md_path = md_dir / "Readme.md"
                else:
                    parent_path = '/'.join(page_path.split('/')[:-1])
                    if parent_path:
                        md_dir = converted_dir / parent_path
                        md_dir.mkdir(parents=True, exist_ok=True)
                    else:
                        md_dir = converted_dir
                    md_path = md_dir / f"{sanitize_filename(title)}.md"

                # Write Markdown
                with open(md_path, 'w', encoding='utf-8') as f:
                    f.write(md)

                # Copy attachments
                att_src = export_dir / page_id / "attachments"
                if att_src.exists():
                    att_dest = md_path.parent / f".attachments.{page_id}"
                    att_dest.mkdir(parents=True, exist_ok=True)
                    for att_file in att_src.iterdir():
                        dest_file = att_dest / att_file.name
                        dest_file.write_bytes(att_file.read_bytes())

                # Update state
                ps.status = 'converted'
                ps.path = str(md_path)
                success_count += 1

            except Exception as e:
                logger.error(f"Failed to convert page {title}: {e}")
                ps.status = 'failed'
                ps.error = str(e)
                fail_count += 1

            progress.advance(task)
            save_state(state, output_dir)

    console.print(f"\n[bold green]Conversion complete:[/bold green] {success_count} succeeded, {fail_count} failed")

    if fail_count > 0:
        sys.exit(EXIT_PARTIAL_FAILURE)


@cli.command()
@click.option('--target-parent', default=None, help='Parent folder in Collectives')
@pass_context
def upload(ctx, target_parent):
    """Upload converted Markdown files to Nextcloud Collectives."""
    config = ctx.config
    state = ctx.state
    dry_run = ctx.dry_run
    output_dir = ctx.output_dir

    if target_parent is None:
        target_parent = config.migration.target_parent

    converted_dir = output_dir / "converted"

    # Find pages to upload
    pages_to_upload = [
        ps for ps in state.pages.values()
        if ps.status == 'converted'
    ]

    if not pages_to_upload:
        console.print("[yellow]No pages to upload. Run 'convert' first.[/yellow]")
        return

    console.print(f"[bold]Uploading {len(pages_to_upload)} pages to Nextcloud[/bold]")

    if dry_run:
        for ps in pages_to_upload:
            console.print(f"  Would upload: {ps.title} -> {target_parent}/{ps.path}")
        return

    try:
        nextcloud = NextcloudClient(config.nextcloud)
    except Exception as e:
        logger.error(f"Failed to connect to Nextcloud: {e}")
        sys.exit(EXIT_AUTH_ERROR)

    # Ensure target parent exists
    nextcloud.ensure_directory(target_parent)

    success_count = 0
    fail_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Uploading pages...", total=len(pages_to_upload))

        for ps in pages_to_upload:
            title = ps.title
            local_path = Path(ps.path)

            progress.update(task, description=f"Uploading: {title[:40]}...")

            try:
                if not local_path.exists():
                    raise FileNotFoundError(f"Converted file not found: {local_path}")

                # Calculate remote path relative to converted directory
                try:
                    relative_path = local_path.relative_to(converted_dir)
                except ValueError:
                    relative_path = Path(local_path.name)

                remote_path = f"{target_parent}/{relative_path}"

                # Upload the markdown file
                nextcloud.upload_file(local_path, remote_path)

                # Upload attachments directory if exists
                att_dir = local_path.parent / f".attachments.{ps.page_id}"
                if att_dir.exists():
                    for att_file in att_dir.iterdir():
                        att_remote = f"{target_parent}/{relative_path.parent}/.attachments.{ps.page_id}/{att_file.name}"
                        nextcloud.upload_file(att_file, att_remote)

                ps.status = 'uploaded'
                success_count += 1

            except Exception as e:
                logger.error(f"Failed to upload page {title}: {e}")
                ps.status = 'failed'
                ps.error = str(e)
                fail_count += 1

            progress.advance(task)
            save_state(state, output_dir)

    console.print(f"\n[bold green]Upload complete:[/bold green] {success_count} succeeded, {fail_count} failed")

    if fail_count > 0:
        sys.exit(EXIT_PARTIAL_FAILURE)


@cli.command()
@click.option('--space-id', help='Confluence space key (e.g., WGS) or numeric ID')
@click.option('--page-ids', help='Comma-separated list of page IDs to migrate')
@click.option('--all', 'all_content', is_flag=True, help='Migrate all accessible content')
@click.option('--exclude-images', is_flag=True, help='Skip image attachments')
@click.option('--exclude-attachments', is_flag=True, help='Skip all attachments')
@click.option('--target-parent', default=None, help='Parent folder in Collectives')
@click.pass_context
def migrate(ctx, space_id, page_ids, all_content, exclude_images, exclude_attachments, target_parent):
    """Run full migration pipeline: export, convert, and upload."""
    console.print("[bold]Starting full migration pipeline[/bold]\n")

    # Run export
    console.print("[bold blue]Phase 1: Export[/bold blue]")
    ctx.invoke(
        export,
        space_id=space_id,
        page_ids=page_ids,
        all_content=all_content,
        exclude_images=exclude_images,
        exclude_attachments=exclude_attachments,
    )

    # Run convert
    console.print("\n[bold blue]Phase 2: Convert[/bold blue]")
    ctx.invoke(convert)

    # Run upload
    console.print("\n[bold blue]Phase 3: Upload[/bold blue]")
    ctx.invoke(upload, target_parent=target_parent)

    console.print("\n[bold green]Migration complete![/bold green]")


@cli.command()
@pass_context
def status(ctx):
    """Show migration status."""
    state = ctx.state

    if not state.pages:
        console.print("[yellow]No migration in progress[/yellow]")
        return

    # Count statuses
    status_counts = {}
    for ps in state.pages.values():
        status_counts[ps.status] = status_counts.get(ps.status, 0) + 1

    console.print("[bold]Migration Status[/bold]")
    console.print(f"  Total pages: {len(state.pages)}")
    for status_name, count in sorted(status_counts.items()):
        color = {
            'pending': 'yellow',
            'exported': 'blue',
            'converted': 'cyan',
            'uploaded': 'green',
            'failed': 'red',
        }.get(status_name, 'white')
        console.print(f"  [{color}]{status_name}: {count}[/{color}]")

    if state.last_updated:
        console.print(f"  Last updated: {state.last_updated}")

    # Show failed pages
    failed = [ps for ps in state.pages.values() if ps.status == 'failed']
    if failed:
        console.print("\n[bold red]Failed pages:[/bold red]")
        for ps in failed:
            console.print(f"  - {ps.title}: {ps.error}")


@cli.command()
@click.option('--reset-failed', is_flag=True, help='Reset failed pages to pending')
@click.option('--reset-all', is_flag=True, help='Reset all pages to pending')
@pass_context
def reset(ctx, reset_failed, reset_all):
    """Reset migration state."""
    state = ctx.state
    output_dir = ctx.output_dir

    if not state.pages:
        console.print("[yellow]No migration state to reset[/yellow]")
        return

    if reset_all:
        for ps in state.pages.values():
            ps.status = 'pending'
            ps.error = None
        console.print(f"[green]Reset all {len(state.pages)} pages to pending[/green]")
    elif reset_failed:
        count = 0
        for ps in state.pages.values():
            if ps.status == 'failed':
                ps.status = 'pending'
                ps.error = None
                count += 1
        console.print(f"[green]Reset {count} failed pages to pending[/green]")
    else:
        raise click.ClickException("Must specify --reset-failed or --reset-all")

    save_state(state, output_dir)


if __name__ == '__main__':
    cli()
