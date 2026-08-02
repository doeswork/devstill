We need to include other major coding laguanges like rust, ruby, go, sql, etc to the parsing or Structural Chunking and have some generic fallback if we don't have the laguange. Or if a chunk is over 1000 lines of code. Like maybe a ineractive "devstill index --verbose" that lets us know how many good files, meh files and bad files

## Current implementation status

- JavaScript, JSX, TypeScript, and TSX structural parsing is implemented with Tree-sitter.
- Go, Rust, Ruby, SQL, Bash, HTML, CSS, and Java structural parsing is implemented through a shared parser registry.
- Exact functions, classes, methods, named handlers, callable assignments, imports, types, interfaces, enums, and top-level executable expressions receive canonical chunks.
- Whole structural modules remain canonical but are excluded from embeddings.
- Anonymous callbacks remain inside their named owner to avoid noisy search records.
- Basenames and shebangs route extensionless Ruby, Bash, and Python files.
- All Git-visible text is eligible by default; configured exclusions remain authoritative.
- Recognized Markdown-like documentation, plain text, manifests, and configuration files use deterministic exact-source sectioning and can be `GOOD`.
- SVG and other images, fonts, media, archives, compiled artifacts, databases, key material, Devstill metadata, and tracked secret environment files are ignored before parsing.
- Lockfiles, source maps, and generated/minified files are ignored by default; path-specific `files.include_ignored` and the compatible noisy-file override are available.
- Unknown UTF-8 formats use a bounded exact-text fallback as `MEH`, while empty and binary files are reported separately as `SKIPPED`.
- Searchable chunks are limited to 8,000 characters and 400 lines. Oversized structural records keep exact canonical parents and receive bounded child segments.
- Content category, disposition, and `GOOD`/`MEH`/`BAD`/`SKIPPED` quality records persist in SQLite and `.devstill/manifests/quality.json`.
- `devstill files --ignored` explains built-in exclusions, and JSON inventory output includes eligible, ignored, and skipped paths.
- `devstill index --verbose` summarizes good files by category/parser and lists ignored, skipped, yellow, and red files with reasons.
- Parser and pipeline versions are tracked so the first index migrates affected files and the next unchanged index embeds nothing.
- `devstill remove` safely removes a repository integration with preview, confirmation, index-only cleanup, custom-layout preservation, and managed `.gitignore` cleanup.
- `devstill agent` stores and reports repository-local OpenAI-compatible endpoint settings and tests strict structured connectivity without revealing secrets.
- `/agent configure` now provides an in-TUI setup form with Save, Save & Test, and Forget actions. Tokens can persist in a user-only `0600` credential file outside the repository, while the configured environment variable remains the higher-priority override.
- `devstill plan` now retrieves five context chunks by default and produces a validated six-label file-impact plan without editing source.
- Successful plans overwrite `.devstill/.plan.md` and `.devstill/.plan.json`; validation failures preserve prior artifacts and receive at most one repair request.
- Agent HTTP attempts append sanitized diagnostics and complete raw response bodies to `.devstill/logs/agent.jsonl`; API keys and transmitted source content are never logged.
- Running `devstill` now opens a prompt-focused Textual workspace with a scrollable session feed, responsive command sidebar, slash-command completion, and command history.
- The TUI prompt is an aligned multiline composer: Enter submits, Shift+Enter inserts a line break, and the editor grows to six rows before scrolling.
- Tab completes the highlighted slash-command suggestion, and `/index` displays its current stage plus determinate current/total progress when available.
- `Ctrl+C` copies selected TUI text and exits when nothing is selected; `Ctrl+Q` remains an unconditional quit binding.
- The TUI opens Chroma and warms one shared query encoder during startup, then reuses both across search, context, plan, and index commands; the sidebar exposes cold/loading/warm state and shutdown releases the session cache.
- `/plan` immediately shows locally retrieved files as white pending items, then replaces that entry in place with validated colored labels and one-sentence agent reasons.
- Large embedding runs now emit real grouped current/total progress with an ETA, and Chroma upserts/deletes respect its runtime batch limit.
- Dirty cross-store indexes are marked as requiring recovery, block incomplete retrieval, and reconcile canonical SQLite search units through the same batched progress path.
- `/index` keeps a compact repository file-count row visible beside the independent active-stage unit progress, including during embedding and dirty recovery.
- Plain TUI input performs local hybrid search; `/context`, `/plan`, `/index`, `/files`, `/agent`, `/agent configure`, and `/agent test` expose the daily workflow without replacing the manual CLI.
- CPU, database, and network commands run through one background worker with visible index progress and recoverable in-session errors.

## Next user checkpoint

```bash
cd ~/projects/devstill/devstill
source activate_devstill.sh
python -m pip install -e ".[dev]"
python -m pip check

cd ~/projects/candid_name
devstill files --ignored
devstill index --verbose
devstill index
cat .devstill/manifests/quality.json

devstill agent configure \
  --base-url http://127.0.0.1:8080/v1 \
  --model YOUR_MODEL
devstill agent test
devstill plan "add an activation code to registered users" --json
cat .devstill/.plan.md
cat .devstill/.plan.json

devstill
# In the TUI, use /agent configure to save and test the endpoint, model, and token.
# Then type /help or start with a normal search question.
```

Future parser registry additions include C/C++, C#, PHP, Kotlin, and Swift.
