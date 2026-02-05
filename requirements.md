Project goal is to create a migration tool for pages migration from Atlassian Confluence to Nextcloud Collectives migration tool

Create a PRD.md specification document, as well as README.md for end users on how to use the tool, including how to create required Confluence and Collectives API / access tokens.

In addition to the scope and requirements below use AskUserQuestion tool, to refine the specification.

* Tool needs to be an easy to use set of Python scripts that users can review and adjust to their environments.
* Source for the data migration is Atlassian Confluence - assume it is the current cloud version (not hosted).
* Target for the data migration is Nextcloud Collectives - hosted and managed by user, assume latest version.
* Pages and attachments to be exported from Atlassian Confluence into HTML format - and stored locally in the project folder.
* All standard images are to be supported, as well as PDFs and Office documents attachments.
* Pages and attachments to be uploaded to Collectives using WebDAV as per the Nextcloud API.
* Exported pages to be converted to Markdown format for compatibility with Collectives application in Nextcloud.
* Images within the pages are to be correctly referenced in Markdown documents, so that they are correctly visible in Collectives.
* Script should have support for standard command line options - including help, dry-run, debug logging, exclude images, exclude attachments, and specify non standard output migration log file name.
* Script must provide a summary of the migration run, as well as handle errors correctly and log warnings and errors as per the verbosity level.
* Script should have support for scope command line options - specific pages IDs (separated by commas if more than one), specific space ID, or everything accessible in the source Confluence.
* Script should also allow to specify target parent page in Collectives, under which it will create new migrated pages (default to be MigratedPages).
* Migration Tool must retain the same hierarchy of pages as it was in Confluence Space, so that created pages in Collectives are structured in the same way, all Confluence Space pages to be nested under the main page of the Space.
* README.md to outline high-level logic of how this tool works, what methods it is using on both source and target sides, and any caveats / limitations / requirements.
