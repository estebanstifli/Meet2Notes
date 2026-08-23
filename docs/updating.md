# Safe updates

Meet2Notes checks GitHub once every 24 hours for a newer stable Release. The check
runs before the local server starts, has a short timeout, sends no meeting data,
and is silently skipped when GitHub is unavailable. Declining an update defers
the same notification for another 24 hours.

Only stable tags in the form `vX.Y.Z` from
`https://github.com/estebanstifli/Meet2Notes.git` are accepted. Development
commits from `main`, drafts, and prereleases are never offered to users.

## What is preserved

- `app.db`: settings, meetings, transcripts, summaries, speakers, jobs, RAG,
  webhooks, plugins, and Live Assistant state.
- The complete data directory, including recordings and generated documents.
- The models directory and downloaded AI models.
- `.env`, the existing `.venv`, and ignored local files.

New settings fields are merged with defaults at runtime. Existing values and
unknown keys are kept. Database schema changes must always be added as a new,
forward-only numbered migration; an applied migration must never be edited.

## Update transaction

`update.bat` refuses to continue while Meet2Notes is running, when tracked or
untracked source changes exist, when the remote is not the official repository,
or when the release is not a fast-forward from the installed revision.

Before changing source code, it uses SQLite's backup API to create:

```text
<data-dir>/backups/pre-update-<old>-to-<new>-<timestamp>.db
```

It then downloads the exact Release tag, updates Python dependencies without
forcing model or PyTorch reinstallation, runs `pip check`, and applies all new
database migrations to a disposable copy of the backup. The real `app.db` is
not migrated until the validated application starts. A source or dependency
failure restores the previous Git revision.

The backup is intentionally retained after success so a user can recover data
manually if a later runtime problem is discovered.

## Maintainer release process

1. Update the version in `pyproject.toml` and
   `src/local_meeting_ai/__init__.py`.
2. Merge to `main` and wait for the `quality` workflow to pass.
3. Run the manual `release` workflow with the exact version, such as `0.6.0`.
4. The workflow re-runs Ruff, mypy, and pytest before publishing `v0.6.0` with
   generated release notes.

The updater begins offering the release after GitHub publishes it. No update is
offered while the repository has no stable Releases.
