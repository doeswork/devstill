We need to include other major coding laguanges like rust, ruby, go, sql, etc to the parsing or Structural Chunking and have some generic fallback if we don't have the laguange. Or if a chunk is over 1000 lines of code. Like maybe a ineractive "devstill index --verbose" that lets us know how many good files, meh files and bad files

## Current implementation status

- JavaScript, JSX, TypeScript, and TSX structural parsing is implemented with Tree-sitter.
- Go, Rust, Ruby, SQL, Bash, HTML, CSS, and Java structural parsing is implemented through a shared parser registry.
- Exact functions, classes, methods, named handlers, callable assignments, imports, types, interfaces, enums, and top-level executable expressions receive canonical chunks.
- Whole structural modules remain canonical but are excluded from embeddings.
- Anonymous callbacks remain inside their named owner to avoid noisy search records.
- Basenames and shebangs route extensionless Ruby, Bash, and Python files.
- All Git-visible text is eligible by default; configured exclusions remain authoritative.
- Unknown UTF-8 formats use a bounded exact-text fallback, while binaries are reported and skipped.
- Searchable chunks are limited to 8,000 characters and 400 lines. Oversized structural records keep exact canonical parents and receive bounded child segments.
- Lockfiles, source maps, minified bundles, vendored/generated paths, and generated-do-not-edit files are reported as skipped `MEH` unless explicitly enabled.
- `GOOD`, `MEH`, and `BAD` quality records persist in SQLite and `.devstill/manifests/quality.json`.
- `devstill index --verbose` summarizes good files by parser and lists every yellow/red file with its reason.
- Parser and pipeline versions are tracked so the first index migrates affected files and the next unchanged index embeds nothing.
- `devstill remove` safely removes a repository integration with preview, confirmation, index-only cleanup, custom-layout preservation, and managed `.gitignore` cleanup.

## Next user checkpoint

```bash
cd ~/projects/devstill/devstill
source activate_devstill.sh
python -m pip install -e ".[dev]"
python -m pip check

cd ~/projects/candid_name
devstill index --verbose
devstill index
cat .devstill/manifests/quality.json
```

Future parser registry additions include C/C++, C#, PHP, Kotlin, and Swift.
