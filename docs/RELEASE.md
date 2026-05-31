# Release Process

Orbit AI uses lightweight tags for releases.

## Checklist

1. Confirm the worktree is clean.
2. Run `make check`.
3. Update `CHANGELOG.md`.
4. Create a tag:

   ```sh
   git tag v0.1.0
   git push origin v0.1.0
   ```

5. Create a GitHub release with:
   - Summary of user-facing changes.
   - Upgrade notes.
   - Privacy/security notes when relevant.
   - Verification command output.

## Versioning

Before `1.0`, minor versions may include behavior changes. Document migration behavior clearly, especially for SQLite schema changes.
