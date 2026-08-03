devstill
`devstill` is a standalone, local-first repository-intelligence TUI for developers and AI coding agents.
Run `devstill` from any directory inside a configured codebase. It searches upward through parent directories until it finds a `devstill.json`, then opens the TUI for that repository.
The long-term goal is to make `devstill` the fastest and most useful way for a human or local AI agent to understand, search, and navigate a Git repository. Indexing and retrieval stay local; `devstill plan` transmits selected source only to the endpoint the user explicitly configures.
Project goals
`devstill` should provide several complementary search modes:
Exact symbol lookup for known functions, classes, methods, fields, and paths
Fuzzy lookup for partially remembered symbol names
Full-text and lexical search for exact code phrases
Semantic vector search for natural-language questions
Relationship traversal between routes, views, models, functions, imports, callers, and tests
LLM-ready context assembly that returns only relevant source chunks
Optional OpenAI-compatible agent workflows for plans, labels, summaries, and ambiguous chunk decisions
Embeddings are only one part of the system. The intended architecture combines deterministic parsing, exact indexes, lexical search, vector search, and code relationships.
Current status
The current release implements:
Global `devstill` console command
Dedicated Python virtual environment
CPU-only PyTorch installation
Local `jina-embeddings-v2-base-code` model
ChromaDB dependency and health check
`devstill init`
`devstill doctor`
`devstill doctor --load-model`
Git-aware `devstill files`
Deterministic structural parsing for Python, JavaScript, JSX, TypeScript, TSX,
Go, Rust, Ruby, SQL, Bash, HTML, CSS, and Java
Bounded exact-text fallback for unknown formats and failed structural parses
Template-aware Django/Jinja HTML fallback
Content-aware documentation and configuration parsing
Built-in asset, sensitive-file, binary, and noisy/generated-file exclusion
Persistent GOOD/MEH/BAD/SKIPPED index-quality reporting
Incremental `devstill index`
Canonical SQLite and FTS5 storage
Persistent Chroma vector storage
Hybrid `devstill search`
Compact Markdown and JSON `devstill context`
OpenAI-compatible repository agent configuration and connectivity testing
Advisory, validated `devstill plan` file-impact classification
Two-pass `devstill deep-plan` retrieval and plan refinement
Controlled Pi-backed Plan execution with patch review and explicit approval
Upward discovery of `devstill.json`
Interactive Textual workspace with a prompt, session feed, command palette, and sidebar
Per-repository configuration
Shared global embedding model
Per-repository `.devstill/` data directory
The following major features are not implemented yet:
Code relationship graph
Broader local-LLM enrichment beyond file-impact planning
Token-budgeted and relationship-aware context expansion
Additional native parsers for C/C++, C#, PHP, Kotlin, and Swift
File watching
Running `devstill` opens the interactive repository workspace. The manual CLI remains available for scripts, advanced filters, rebuilds, configuration, and removal.
Development repository layout
The current development checkout is expected to look like:
```text
~/projects/devstill/devstill/
├── .venv/
├── README.md
├── ToDo.md
├── activate_devstill.sh
├── devstill.example.json
├── pyproject.toml
├── setup_wsl_cpu.sh
└── src/
    └── devstill/
        ├── __init__.py
        ├── cli.py
        ├── config.py
        ├── tui_commands.py
        └── tui.py
```
The development checkout currently happens to be nested under:
```text
~/projects/devstill/devstill/
```
Use that directory as the Devstill source root unless the project is later moved.
Install on WSL
From the Devstill source directory:
```bash
cd ~/projects/devstill/devstill

chmod +x setup_wsl_cpu.sh
./setup_wsl_cpu.sh
source ~/.bashrc
```
This one command also installs the Pi coding executor and a compatible Node.js
runtime when needed. Pi is installed through its official installer and is used
only when `devstill execute` or `/execute` runs. For a search-only installation:
```bash
DEVSTILL_SKIP_PI=1 ./setup_wsl_cpu.sh
```
The installer creates one dedicated environment:
```text
~/projects/devstill/devstill/.venv/
```
The shared embedding model is stored at:
```text
~/.local/share/devstill/models/jina-embeddings-v2-base-code/
```
The global command is linked into:
```text
~/.local/bin/devstill
```
Confirm that the command is available:
```bash
which devstill
devstill --version
pi --version
```
Expected version:
```text
devstill 0.1.0
```
Activate the development environment
The global `devstill` command can be used without manually activating the venv.
Activate the venv when modifying or testing Devstill itself:
```bash
cd ~/projects/devstill/devstill
source .venv/bin/activate
```
Or use:
```bash
source ~/projects/devstill/devstill/activate_devstill.sh
```
Dependency compatibility
The Jina embedding model uses custom Transformers code and currently requires Transformers 4.x.
The project dependency must remain:
```toml
"transformers>=4.56,<5"
```
Do not allow Transformers 5.x until the Jina model's custom code is confirmed compatible.
JavaScript-family parsing uses compatible Tree-sitter language ABI versions:
```toml
"tree-sitter>=0.25,<0.26"
"tree-sitter-bash>=0.25.1,<0.26"
"tree-sitter-css>=0.25,<0.26"
"tree-sitter-go>=0.25,<0.26"
"tree-sitter-html>=0.23.2,<0.24"
"tree-sitter-java>=0.23.5,<0.24"
"tree-sitter-javascript>=0.25,<0.26"
"tree-sitter-ruby>=0.23.1,<0.24"
"tree-sitter-rust>=0.24.2,<0.25"
"tree-sitter-sql>=0.3.11,<0.4"
"tree-sitter-typescript>=0.23.2,<0.24"
```
Agent requests use a directly pinned HTTP client:
```toml
"httpx>=0.28,<1"
```
The environment currently uses:
```text
Python 3.12
CPU-only PyTorch
Transformers 4.x
Sentence Transformers
ChromaDB
Textual
```
The warning about `optimum` not being installed is harmless for normal embedding generation. Optimum is only needed for ONNX-specific workflows.
Verify the installation
Basic health check:
```bash
devstill doctor
```
Expected checks include:
```text
devstill version
Python version
CPU-only PyTorch
CUDA unavailable
embedding-model path
Chroma heartbeat
Pi and Node.js executor status
```
Full model-loading test:
```bash
devstill doctor --load-model
```
The model should produce a 768-dimensional embedding:
```text
Embedding shape: (1, 768)
```
The model has already been verified to work fully offline when loaded with:
```python
SentenceTransformer(
    model_path,
    trust_remote_code=True,
    local_files_only=True,
    device="cpu",
)
```
Configure a codebase
Go to any Git repository that should be indexed:
```bash
cd ~/projects/my-codebase
devstill init
```
This creates:
```text
my-codebase/
├── devstill.json
└── .devstill/
```
It also adds this entry to the repository's `.gitignore`:
```gitignore
.devstill/
```
The current workflow is:
```bash
cd ~/projects/my-codebase

devstill init
devstill index
devstill
```
`devstill index` is incremental by default. Use `devstill index --rebuild` to rebuild the managed SQLite and Chroma indexes.

Remove Devstill from a repository with a preview and interactive confirmation:
```bash
devstill remove --dry-run
devstill remove
```
Full removal deletes the repository's Devstill index, `devstill.json`, and only
the exact `.gitignore` block added by `devstill init`. It does not remove the
global Devstill/Pi commands, virtual environment, shared embedding model, or another
repository's index. To discard only generated index data while keeping the
repository configured, use:
```bash
devstill remove --index-only
```
Noninteractive scripts must pass `--yes`. Custom index layouts remove only
known Devstill artifacts and preserve unrelated sibling files.
Configuration discovery
Devstill searches upward for `devstill.json`.
For example:
```text
~/projects/my-codebase/devstill.json
~/projects/my-codebase/src/components/
```
Running this from the nested directory:
```bash
cd ~/projects/my-codebase/src/components
devstill
```
should discover:
```text
~/projects/my-codebase/devstill.json
```
Example `devstill.json`
```json
{
  "version": 1,
  "repo": {
    "root": "."
  },
  "index": {
    "path": ".devstill/chroma",
    "collection": "devstill_code",
    "max_chunk_chars": 8000,
    "max_chunk_lines": 400,
    "fallback_overlap_lines": 20
  },
  "embedding": {
    "model": "/home/USER/.local/share/devstill/models/jina-embeddings-v2-base-code",
    "device": "cpu",
    "max_sequence_length": 2048,
    "batch_size": 8,
    "normalize_embeddings": true
  },
  "files": {
    "use_gitignore": true,
    "scan_all_text": true,
    "index_noisy_files": false,
    "include_ignored": [],
    "include": [
      "**/*.py",
      "**/*.js",
      "**/*.jsx",
      "**/*.ts",
      "**/*.tsx",
      "**/*.go",
      "**/*.rs",
      "**/*.rb",
      "**/*.rake",
      "**/*.gemspec",
      "**/*.sh",
      "**/*.bash",
      "**/*.java",
      "**/*.json",
      "**/*.toml",
      "**/*.yaml",
      "**/*.yml",
      "**/*.sql",
      "**/*.html",
      "**/*.css",
      "**/*.md",
      "**/*.markdown",
      "**/*.rst",
      "**/*.adoc",
      "**/*.txt",
      "**/*.jsonc",
      "**/*.ini",
      "**/*.cfg",
      "**/*.conf",
      "**/*.service",
      "Dockerfile",
      "Makefile",
      ".gitignore",
      ".env*.example",
      ".env*.sample",
      ".env*.template",
      "Gemfile",
      "Rakefile"
    ],
    "exclude": [
      ".git/**",
      ".devstill/**",
      ".venv/**",
      "venv/**",
      "node_modules/**",
      "dist/**",
      "build/**",
      "__pycache__/**"
    ]
  },
  "llm": {
    "enabled": false,
    "base_url": "http://127.0.0.1:8080/v1",
    "model": "",
    "api_key_env": "DEVSTILL_AGENT_API_KEY",
    "timeout_seconds": 300,
    "max_output_tokens": 2048,
    "planning_max_output_tokens": 6144,
    "planning_reasoning": "off"
  }
}
```
Intended repository indexing behavior
The indexer should use Git as the authority for file discovery.
Recommended command:
```bash
git ls-files -co --exclude-standard -z
```
This returns:
Tracked files
Untracked files not ignored by Git
Files permitted by repository ignore rules
Devstill should additionally apply the `files.include` and `files.exclude` patterns from `devstill.json`.
By default, `files.scan_all_text` makes every Git-visible path eligible after
built-in and configured exclusions. Set it to `false` to use `files.include`
as an allowlist. `files.exclude` always wins.

After configured exclusions, Devstill applies its own content policy. Source
code, recognized documentation, and useful configuration are eligible for
indexing. Images (including SVG), fonts, media, archives, compiled artifacts,
databases, key material, tracked secret environment files, Devstill metadata,
lockfiles, Gradle wrapper scripts, source maps, and generated/minified files are
ignored by default.
Use `devstill files --ignored` to see exact paths and reasons.

`files.include_ignored` can rescue specific paths, such as an architecture SVG
whose embedded text is intentionally useful. It cannot override
`files.exclude` or hard safety exclusions. `files.index_noisy_files` remains a
compatibility switch for noisy/generated files only; it does not enable assets.
The indexer must not scan:
`.git/`
`.devstill/`
Virtual environments
`node_modules/`
Build output
Generated caches
Binary files
Intended chunking rules
Chunks must follow source structure, not arbitrary character length.
Python uses deterministic `ast` parsing. JavaScript, JSX, TypeScript, TSX, Go,
Rust, Ruby, SQL, Bash, HTML, CSS, and Java use their corresponding Tree-sitter
grammars. Extensionless Bash, Ruby, and Python scripts can be routed by
shebang, and common files such as `Gemfile` are routed by basename.
Candidate chunk types include:
Module
Function
Async function
Class
Method
Nested function
Assignment or constant
Import group
Decorated definition
Type alias, interface, or enum
Model or schema field
Route declaration
Test
Configuration block
A function chunk must end exactly at the function boundary and must never include the beginning of the next function.
Decorators belong to the decorated definition.
Comments immediately above a definition should be attached only when they clearly describe that definition. Broad section headings and module comments should remain separate context.
Named nested handlers and named arrow functions receive their own chunks.
Anonymous callbacks remain inside their owning function or component. Each
JavaScript-family file retains a non-searchable module record, while its exact
structural chunks receive search vectors.
Every searchable record is bounded by both `index.max_chunk_chars` and
`index.max_chunk_lines`. Oversized structural records retain a complete
non-searchable canonical parent and receive exact bounded child segments.
Markdown, reStructuredText, AsciiDoc, and plain text retain complete canonical
documents and receive exact heading-aware sections. JSON/JSONC, YAML, TOML,
INI-style files, manifests, dependency lists, service files, web-server
configuration, `.gitignore`, Dockerfiles, and Makefiles receive exact logical
configuration sections. These recognized formats can be `GOOD`.

Unknown UTF-8 formats retain a complete canonical document and receive
paragraph/heading-aware bounded segments as `MEH`. When a known structural parser
reports an error but the UTF-8 source can still be bounded safely, Devstill retains
the diagnostic and indexes exact fallback chunks as `MEH`; parser errors therefore
do not necessarily imply `BAD`. Django/Jinja HTML uses exact template block and
interstitial sections for this recovery. Invalid UTF-8 and unsegmentable fallback
content remain `BAD`. Ignored assets and noisy files have no canonical chunks or
embeddings. Empty and binary files are tracked as `SKIPPED`, outside the
GOOD/MEH/BAD totals.
The local LLM may later review ambiguous comment attachment, but deterministic parsers remain the authority for source boundaries.
Canonical chunks versus search units
Devstill should distinguish between exact source records and embedding records.
Canonical chunks
Canonical chunks are exact source objects:
```text
UserProfile
UserProfile.bio
UserProfile.avatar
UserProfile.get_display_name
```
They should be stored in SQLite with exact line ranges and source text.
Search units
Search units are coherent pieces of information that receive embeddings.
Several tiny canonical chunks may be grouped into one search unit. For example, several fields in one class may form a single schema-search unit while remaining individually addressable through the exact-symbol index.
Large functions may keep one canonical source chunk but have several semantic search views that point back to the same function.
Embedding behavior
The shared embedding model is:
```text
jinaai/jina-embeddings-v2-base-code
```
It converts different-length inputs into fixed 768-dimensional vectors.
Recommended initial settings:
```text
device: cpu
max sequence length: 2048
batch size: 8
normalized embeddings: true
```
The model supports variable input lengths. Each search unit should represent one coherent idea rather than merely trying to approach the maximum sequence length.
The embedding input may contain enriched searchable context:
```text
Repository path
Symbol kind
Qualified symbol
Parser-confirmed roles
LLM-generated summary
Natural-language concepts
References
Exact source
```
The returned canonical source must remain exact code without synthetic text mixed into it.
Intended storage architecture
Chroma should be used for semantic vector search.
SQLite should eventually store:
Canonical chunks
Exact symbol index
Aliases
File hashes
Search-unit membership
Relationship edges
LLM enrichment cache
SQLite FTS5 full-text index
Recommended per-repository storage:
```text
.devstill/
├── chroma/
├── metadata.sqlite
├── .context.md
├── .context.json
├── .plan.md
├── .plan.json
├── .deep-plan-initial.md
├── .deep-plan-initial.json
├── .deep-plan.md
├── .deep-plan.json
├── .execute.patch
├── .execute.json
├── cache/
├── logs/
└── manifests/
    ├── index.json
    └── quality.json
```
Intended Chroma records
A Chroma record should include:
```python
{
    "id": "accounts/models.py::UserProfile.bio::model_field",
    "embedding": [0.01, -0.02, "..."],
    "document": "bio = models.TextField(blank=True)",
    "metadata": {
        "path": "accounts/models.py",
        "filename": "models.py",
        "language": "python",
        "kind": "model_field",
        "name": "bio",
        "qualname": "UserProfile.bio",
        "parent": "UserProfile",
        "start_line": 15,
        "end_line": 15,
    },
}
```
The enriched text used to create the vector does not need to be returned to the LLM.
Intended search modes
Devstill should eventually expose:
```bash
devstill index
devstill index --rebuild
devstill search "where are user profile details stored?"
devstill search "permission_classes" --exact
devstill symbol UserProfile
devstill symbol userProf --fuzzy
devstill related accounts.models.UserProfile
devstill context "explain the profile update flow"
devstill watch
```
The TUI should expose equivalent workflows.
Search should combine:
Exact symbol lookup
Prefix lookup
RapidFuzz symbol matching
SQLite FTS5/BM25 lexical search
Chroma semantic search
Path matching
Relationship expansion
Result deduplication
Per-file diversity limits
Local LLM integration
`devstill plan` calls a configured OpenAI-compatible Chat Completions endpoint.
Endpoint and model configuration are repository-local. API tokens are never written
to `devstill.json` or displayed. The interactive TUI can remember a token in
`~/.config/devstill/credentials.json`, an atomic user-only file with `0600`
permissions. The configured environment variable remains supported and takes
precedence over a remembered token. Embedded URL credentials are rejected.

Plan and Deep Plan use a separate 6,144-token output ceiling; this is a maximum, so
models can stop earlier. General agent calls retain the smaller LLM
`max_output_tokens` limit, while Pi execution uses
`executor.max_output_tokens` and defaults to 49,152 tokens. Planning requests
default to `planning_reasoning: "off"` and use Qwen's
`chat_template_kwargs.enable_thinking=false`. If a compatible server rejects that
extension, Devstill retries without it. The client also requests strict JSON Schema
output and falls back when structured output is unsupported, using at most three
compatibility attempts total.

Every HTTP attempt appends a sanitized diagnostic record to
`.devstill/logs/agent.jsonl`. The log includes response status, elapsed time, token
ceiling, reasoning mode, fallback count, finish reason, usage, and the complete
response body so empty, truncated, or malformed local model output can be diagnosed.
It records message roles and character counts rather than transmitted source, and
never records authorization headers or API keys.

Configure and verify an agent from anywhere beneath the repository:
```bash
devstill agent configure \
  --base-url http://127.0.0.1:8080/v1 \
  --model local-model \
  --planning-max-output-tokens 6144 \
  --planning-reasoning off
export DEVSTILL_AGENT_API_KEY="your-key-if-required"
devstill agent
devstill agent test
```
Alternatively, run `devstill`, choose **Agent setup** in the right sidebar, or type
`/agent configure`. Enter the endpoint, model, and token, leave **Remember** enabled,
then choose **Save & Test**. The password field is always blank when reopened and the
saved token is never rendered back to the terminal. **Forget saved token** removes
the credential for the current endpoint; it does not unset an environment variable.

`devstill agent enable` and `devstill agent disable` change only the repository
configuration. Any HTTP(S) endpoint is accepted, so review the displayed network
destination before sending repository context. Requests use
`POST {base_url}/chat/completions`, following the
[OpenAI Chat Completions structured-output shape](https://platform.openai.com/docs/api-reference/chat).

The agent's broader future responsibilities can include:
Generating concise source-grounded summaries
Producing natural-language labels and concepts
Reviewing ambiguous comment attachment
Recommending grouping of tiny chunks
Creating search views for large multi-purpose functions
Classifying semantic roles
Helping assemble answer-ready context
The LLM must not overwrite parser-confirmed facts such as:
File path
Symbol name
Line numbers
Parent symbol
Decorators
Imports
AST-confirmed references
LLM output should use strict JSON, be validated with Pydantic, and be cached by content hash, model name, and prompt version.
File-impact plans are validated now; enrichment caching remains future work.

Plan execution

`devstill execute` uses Pi as a short-lived implementation engine. Devstill selects
the newest `.plan.json` or `.deep-plan.json` unless `--from` is provided, rejects
plans with unresolved `Search` entries, and stages only the approved files in an
isolated temporary Git repository. Pi runs with reasoning disabled, no session or
repository context files, a bounded tool budget, and no shell tool.

Blue `Read` targets are copied for reference but cannot be changed. Existing
`Modify`, `Add`, and `Delete` targets may change, while `New File` may create only
its exact safe repository-relative path. Devstill rejects symlinks, traversal,
unlisted files, and any read-only change before writing `.devstill/.execute.patch`.

The patch is displayed before application. Interactive CLI and TUI use explicit
confirmation; `--dry-run` only generates the preview and `--yes` is required for
noninteractive application. Devstill rechecks original file hashes and runs
`git apply --check` immediately before applying. It never commits or pushes.

After a successful `/plan` or `/deep-plan`, the TUI asks whether to implement that
plan with Pi without opening a popup. The bottom composer accepts optional multiline
execution instructions; leaving it blank reuses the Plan's original query. The
colored status on each existing file is an inline dropdown for `Read`, `Modify`,
`Delete`, or `Add`, and valid changes are saved immediately without changing the
one-sentence reason. Choosing a concrete label for a white `Search` row temporarily
uses the composer for its exact repository-relative path. Pi remains blocked until
every `Search` entry is resolved.

The composer shows focused, right-aligned actions for **Not now**, **Preview with
Pi**, **Use file**, **Cancel**, and **Apply patch** as appropriate. Normal commands
pause during these focused steps. The complete patch and approval warning stay in
the main feed; only an explicit **Apply patch** click can approve it. Custom Pi
instructions cannot add writable paths or weaken the Plan's labels.

Optional validation commands are configured as argument strings without shell
expansion:
```json
"executor": {
  "enabled": true,
  "backend": "pi",
  "timeout_seconds": 300,
  "max_output_tokens": 49152,
  "max_tool_calls": 16,
  "validation_commands": ["npm run typecheck", "python3 -m pytest -q"]
}
```
Only these user-configured commands run after application. A validation failure
leaves the reviewed changes in place and is recorded in `.execute.json`; Pi events
are written to `.devstill/logs/pi-execute.jsonl` without API credentials.
Venv rule
Keep all Devstill client-side functionality in the single dedicated venv:
```text
Textual TUI
parsers
CPU embedding model
Chroma
SQLite
fuzzy search
indexer
search engine
LLM HTTP client
```
Do not install Devstill into each scanned repository's environment.
A future Python/CUDA LLM server should use a separate venv or container.
The following already run as separate services and do not need a Devstill-specific venv:
llama.cpp server
Ollama
LM Studio
Current commands
```bash
devstill
devstill init
devstill init --force
devstill remove
devstill remove --dry-run
devstill remove --index-only
devstill remove --yes
devstill doctor
devstill doctor --load-model
devstill files
devstill files --count
devstill files --json
devstill files --ignored
devstill parse path/to/file.py
devstill parse path/to/file.py --json
devstill parse frontend/src/App.tsx
devstill parse frontend/src/App.tsx --json
devstill parse src/main.rs --json
devstill index
devstill index --rebuild
devstill index --verbose
devstill index --json
devstill search "where is authentication checked?"
devstill search UserProfile --exact
devstill search "authentication checks" --lexical-only
devstill search "where is the profile stored?" --semantic-only
devstill context "explain the authentication flow"
devstill context "explain the authentication flow" --path src/ --json
devstill context "explain test coverage" --include-tests
devstill agent
devstill agent configure --base-url http://127.0.0.1:8080/v1 --model local-model
devstill agent test
devstill agent enable
devstill agent disable
devstill plan "add an activation code to registered users"
devstill plan "add an activation code" --path core/ --limit 5 --json
devstill plan "audit authentication" --include-tests --lexical-only
devstill deep-plan "trace and refine the complete activation change"
devstill deep-plan "improve check-in geolocation" --initial-limit 5 --limit 8 --json
devstill execute --dry-run
devstill execute --from plan
devstill execute --from deep-plan --yes
devstill execute --json --dry-run
devstill --version
```

Interactive TUI

Run `devstill` from anywhere beneath an initialized repository. The prompt accepts
ordinary natural-language searches and slash commands:

```text
where is authentication checked?
/search registration flow
/context explain the login session
/plan add account activation
/deep-plan trace the complete account activation change
/execute
/execute plan
/execute deep-plan
/index
/files
/agent
/agent configure
/agent test
/help
/clear
/quit
```

Typing `/` opens command suggestions. Use Up and Down to highlight one and Tab to
complete it in the prompt without executing it. The right sidebar offers the same
commands and also prefills the prompt. Search runs locally; `/plan`, `/deep-plan`,
`/execute`, and `/agent test` contact the configured endpoint. Context, Plan, Deep
Plan, and execution commands keep the same artifact behavior as the CLI. Successful
Plan and Deep Plan commands open an optional Pi handoff: enter a more specific
execution request, or submit it blank to reuse the original planning query. Pi
handoff, label review, Search resolution, and patch approval stay inline: Plan
statuses are clickable dropdowns in the feed and action buttons appear at the
bottom-right of the composer. `/agent configure` remains a separate setup form.

Human-readable Search, Context, Plan, Deep Plan, index, and execution results use a
compact layout. Each Plan item keeps its label and path on one line with its reason
immediately below it, without a blank spacer row. Decorative blank rows are omitted,
and exact source retains only its real line breaks inside code fences. JSON output is
unchanged.

The prompt is a compact multiline editor aligned with its leading marker. `Enter`
submits and `Shift+Enter` inserts a line break. It grows from one to six visible
rows, then scrolls longer requests internally.

`/agent configure` opens an in-app setup form for the OpenAI-compatible base URL,
model, token, environment-variable name, timeout, general/Pi output-token limit,
planning output-token limit, and planning reasoning mode. **Save & Test** remembers
the settings and immediately verifies the connection. Remembered
credentials are global to the current operating-system user and are intentionally
outside repository removal; use **Forget saved token** to remove one. Setting the
configured API-key environment variable overrides the remembered value for that
process.

Keyboard shortcuts are `Ctrl+Q` to quit, `Ctrl+L` to clear the session, `Ctrl+B` to
toggle the responsive sidebar, and `Ctrl+R` to refresh status. `Ctrl+C` copies when
text is selected and exits when there is no selection. Up and Down recall commands
for a single-line prompt when the slash palette is closed; multiline prompts retain
normal vertical cursor movement. The feed keeps the latest 50 commands in memory
and is not persisted.

During `/index`, the composer displays the active discovery, reconciliation,
parsing, embedding, or persistence stage. Stages with measurable work show a
progress bar, percentage, and current/total count; the final session entry retains
the incremental-index and quality summary. CPU embedding is grouped for structured
progress updates, so large repositories advance throughout the encoder run and show
an ETA instead of remaining at zero until the whole model call returns.

A separate compact `FILES` row remains visible throughout indexing. It reports total,
checked, changed, unchanged, and parsed file counts while the stage row independently
tracks items such as search units or Chroma vectors. Dirty-index reconciliation seeds
the total from canonical SQLite files before vector rebuilding begins. Repository
checking is throttled to roughly 200 UI updates even for very large inventories.

Chroma writes and deletes are split using the database client's runtime maximum
batch size. If an index update is interrupted or a Chroma batch fails, Devstill
retains `.indexing-dirty`, reports the repository as requiring recovery, and refuses
search/context/plan retrieval from the incomplete cross-store state. Running
`devstill index` or `/index` reconciles vectors from canonical SQLite records; the
summary reports reconciled units separately from newly embedded changed-file units.

The TUI opens Chroma and warms the local query encoder in a startup worker whenever
the repository already has an index. Startup may spend several seconds loading the
CPU model and raises Devstill's memory use by roughly 1.1 GiB, but the first search,
context, or plan then avoids that cold-start delay. The sidebar reports the encoder
as `cold`, `loading`, or `warm`. The same encoder and Chroma client are reused for
the rest of that TUI session, while each command keeps its own lightweight SQLite
connection. Missing indexes remain cold until `/index` builds them. Closing Devstill
releases the session cache. One-shot CLI commands remain independent and therefore
still pay their own cold-start cost.

`/plan` renders in two stages. As soon as local vector retrieval finishes, the feed
shows each unique retrieved file with a white pending marker and its matched symbols.
That same entry is replaced in place with the agent's colored impact labels and
one-sentence reasons after validation succeeds. Agent failures keep the retrieved
file list visible, attach the error and response-log path, and do not replace
`.plan.*`.

`/deep-plan` leaves ordinary Plan unchanged. It first builds an adaptive three-to-five
chunk initial plan, then batch-embeds up to three focused generated reasons and
expands the pinned context to five-to-eight total chunks. Follow-up matches are
globally fused across those searches rather than appended round-robin. The feed keeps
the colored initial plan visible while newly discovered existing files appear as
white pending entries, then replaces the entry
with a fully validated refined plan. Initial and final results are saved separately
as `.deep-plan-initial.*` and `.deep-plan.*`.

`/execute` runs Pi in the background against the newest successful plan and shows
the complete staged patch in the session feed. Inline **Cancel** and **Apply patch**
buttons appear beside the bottom composer before Devstill rechecks and applies it;
submitting the composer never approves a patch. `/execute plan` and `/execute
deep-plan` select a specific artifact. Cancelling closes the staging workspace
without changing repository files.
`devstill context` returns five records by default. Every successful context command
also overwrites `.devstill/.context.md` and `.devstill/.context.json` with both
reviewable formats, regardless of which format is printed to stdout.

`devstill plan` retrieves three-to-five chunks adaptively, stopping after a clear
relevance drop while favoring multi-channel matches and file diversity. It groups
chunks by repository path and asks the configured model to classify file impact. It
returns `Read`,
`Modify`, `Delete`, `Add`, `Search`, and `New File` items with one short reason.
The command is advisory: it never changes source files. Every successful run
overwrites `.devstill/.plan.md` and `.devstill/.plan.json` without changing the
`.context.*` artifacts. Invalid model output receives one repair request; if that
also fails, the command exits nonzero and preserves the previous plan artifacts.
The network destination and transmitted chunk count are written to stderr, so
`--json` keeps stdout machine-readable.

`devstill deep-plan` exposes the same filters and retrieval modes, with
`--initial-limit 5` and `--limit 8` defaults. The adaptive initial three-to-five
chunks remain pinned; up to three prioritized reason searches fill the remaining
capacity through global reciprocal-rank fusion, score-gap stopping, file diversity,
and exact-chunk deduplication. Query embeddings for those searches are generated in
one CPU batch. A failed initial pass preserves every previous Deep Plan artifact. A
failed refinement saves the new initial result while preserving the previous final
`.deep-plan.*` files. Normal `.plan.*` and `.context.*` files are never changed by
Deep Plan.

Parser, pipeline, and content-policy versions are tracked. After installing an
upgrade, a normal `devstill index` reparses files affected by the changed
fingerprint; `--rebuild` is not required.

Every index run prints content coverage, colored `GOOD`, `MEH`, and `BAD`
totals, plus separate ignored, noisy/generated, empty, and binary counts.
`devstill index --verbose` summarizes good files by category/parser and lists
all ignored, skipped, yellow, and red files with reasons on stderr. The complete
per-file report is written to `.devstill/manifests/quality.json`; `index --json`
adds the same counts without removing existing fields.

Development commands
```bash
cd ~/projects/devstill/devstill
source .venv/bin/activate

python -m pip install -e ".[dev]"
python -m pip check

pytest
ruff check .
```
See `ToDo.md` for the implementation sequence and acceptance criteria.
