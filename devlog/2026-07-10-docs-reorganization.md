# Devlog: Documentation Reorganization

Updated: 2026-07-10
Author: Peinian Li

## Summary

This update reorganizes FocusLedger's documentation so each document has a clearer responsibility. The README now acts as a project-level entry point, while command-level usage is maintained in a dedicated CLI guide.

## Changes

### README Scope

The README was simplified into a high-level project overview. It now keeps:

- project purpose
- documentation links
- current data pipeline
- platform support
- attention model summary
- privacy model
- `(unknown)` timeline explanation
- current non-goals / not-yet-implemented items
- test commands

Detailed command instructions were removed from README to avoid duplication and drift.

### CLI Usage Guide

A new CLI guide was added:

```text
docs/CLI_USAGE_ZH.md
```

This document is now responsible for day-to-day operation notes, including:

- initialization
- starting and stopping the agent
- background mode
- status checks
- timeline and summary commands
- attention commands
- date-based queries
- attention configuration
- privacy redaction
- development database reset
- common workflows

Future CLI operation hints should be added there instead of README.

### Docs Directory

The proposal and phase planning documents were moved into `docs/`:

```text
docs/PROJECT_PROPOSAL_ZH.md
docs/DEVELOPMENT_PHASES_ZH.md
docs/CLI_USAGE_ZH.md
```

README now links to these documents from a single "Documentation" section.

## Rationale

The project had started to accumulate operational instructions in README while also needing a separate command manual. Keeping command snippets in both places would make future changes harder, especially as the CLI keeps evolving.

The new split is:

```text
README.md              project overview and navigation
docs/CLI_USAGE_ZH.md   operational command guide
docs/*.md              proposal and planning documents
devlog/*.md            implementation history and design notes
```

This keeps README readable for new contributors and gives the CLI guide enough room to be practical.

## Validation

The test suite was run after the README reorganization:

```bash
python -m unittest discover -s tests
```

Result:

```text
21 tests OK
```
