devstill

Quick runtime: [how the agent loop works](MINIMAL_RUNTIME.md). Quick uses Devstill’s own retrieval-primed agent loop in TUI and headless mode; Pi is a comparison baseline and explicit legacy option.

Evaluation: [latest results against Pi](benchmarks/retrieval/QUICK_LOOP_RESULTS.md) and the
[earlier native Quick test plan](benchmarks/retrieval/TEST_PLAN.md). Run every benchmark
suite unattended with `./pi_vs_devstill_bench.sh` ([how it works](benchmarks/BENCHMARKING.md)). Superseded
plans and reports are preserved in the [historical archive](archive/README.md).

`devstill` is a standalone, local-first repository-intelligence TUI for developers and AI coding agents.
Run `devstill` from any directory inside a configured codebase. It searches upward through parent directories until it finds a `devstill.json`, then opens the TUI for that repository.
The goal is fast local retrieval followed by compact coding tasks. Indexing and embeddings stay local; Task Review and YOLO send selected source only to the configured model endpoint. Task Review and YOLO use local retrieval and Devstill’s native bounded editor. Review shows the actual checked patch; YOLO applies it automatically. Explicit Feature mode tries Quick first and coordinates only unresolved reviews; feature quality remains experimental ([comparison results](archive/benchmarks/retrieval/FEATURE_COORDINATION_RESULTS.md)).
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
Automatic CPU/CUDA PyTorch installation with explicit overrides
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
Install on Ubuntu or WSL

Clone Devstill and run the setup script; no manual build is required:
```bash
git clone https://github.com/doeswork/devstill.git
cd devstill
./setup.sh
source ~/.bashrc
```

The default `--device auto` mode detects an NVIDIA GPU, installs Devstill's tested
CUDA PyTorch wheel, and verifies a real GPU allocation. If that fails, setup prints
a warning and installs the CPU wheel. Force either mode with:

```bash
./setup.sh --device cpu
./setup.sh --device cuda
```

CUDA requires a supported NVIDIA GPU and a compatible host driver. Devstill uses
PyTorch's prebuilt wheels, so users do not build PyTorch from source. Advanced users
can select another official wheel index when their driver needs it:

```bash
./setup.sh --device cuda \
  --torch-index-url https://download.pytorch.org/whl/cu128
```

Explicit CUDA setup fails instead of silently using CPU when verification fails.
Automatic mode is the only mode that falls back. Rerun setup to change the installed
PyTorch build; the per-project `devstill device` command cannot add CUDA support to a
CPU-only PyTorch installation.

Before downloading CUDA, setup identifies the private installation destination and
warns that the CUDA-enabled environment commonly occupies roughly 7–10 GiB. These
libraries stay under `~/.local/share/devstill/venv`; they are not installed into
`/opt`, `/etc`, or the system Python. CPU installations are substantially smaller.
Setup disables pip's download cache for all of its package installations and removes
CUDA-only packages when changing to CPU or falling back from failed CUDA verification.

Setup also installs the Pi coding executor and a compatible Node.js runtime when
needed. Pi is used only for `devstill execute` or `/execute`. For search and planning
without code execution, use either form:

```bash
./setup.sh --skip-pi
DEVSTILL_SKIP_PI=1 ./setup.sh
```

The public installation is independent of the cloned source tree:

```text
~/.local/share/devstill/venv/
~/.local/share/devstill/install.json
~/.local/share/devstill/activate.sh
~/.local/share/devstill/models/jina-embeddings-v2-base-code/
~/.local/bin/devstill
```

Setup prints the final environment and embedding-model sizes when it completes. It
never purges pip's shared cache because that cache may belong to other Python
projects. Users upgrading from an older Devstill installer can optionally reclaim
all shared pip downloads after confirming Devstill works:

```bash
~/.local/share/devstill/venv/bin/python -m pip cache purge
```

That command affects pip's user-wide cache, not just Devstill. It does not remove
installed packages or the embedding model.

After setup succeeds, the cloned checkout can be moved or deleted. Confirm the
installation and selected accelerator with:

```bash
which devstill
devstill --version
devstill doctor --load-model
pi --version
```

Update and uninstall

`devstill update` checks the latest stable GitHub Release, shows the current and
target versions, and asks before updating. Scripts must opt in explicitly:

```bash
devstill update --check
devstill update
devstill update --yes
```

The updater is intentionally unavailable in editable developer checkouts. It keeps
the installed PyTorch build and runs package and Devstill health checks afterward.
The update channel becomes usable when the project publishes its first versioned
GitHub Release.

To install local code changes into an existing private installation, close Devstill
and run this from the source checkout:

```bash
./update-local.sh
```

The script includes your current uncommitted edits. You can also invoke it by its
full path from another project. It builds a clean temporary wheel, verifies its
source and Pi policy asset, checks dependency compatibility and startup, then
reinstalls the package even if its version has not changed. Installed startup and
package-integrity checks must pass before it reports success. Reopen Devstill in
your project afterward; no reindexing or configuration changes are required.

This local workflow reuses your existing dependencies, CUDA, models, and Pi. If
the checkout needs incompatible or missing dependencies, the script stops before
installation and directs you to `./setup.sh`. It locates the environment through
`DEVSTILL_VENV`, then the `venv` field in installation metadata, then
`$DEVSTILL_DATA_HOME/venv` (by default `~/.local/share/devstill/venv`, respecting
`XDG_DATA_HOME`). `DEVSTILL_INSTALL_METADATA` can override the metadata path.
Use `./update-local.sh --help` for usage. `devstill update` continues to install
published releases; it does not install local edits.

Generated `build/` and `dist/` directories are ignored and must not be committed.
The local update script builds outside the checkout so stale build files cannot
be mixed with current source.

To uninstall the self-contained client and shared model, remove
`~/.local/bin/devstill` and `~/.local/share/devstill`. This does not remove Pi or any
per-repository `devstill.json`/`.devstill` data. Use `devstill remove` inside each
repository first if that data should also be deleted.

Development installation

Contributors should keep a separate editable environment in the source checkout:

```bash
git clone https://github.com/doeswork/devstill.git
cd devstill
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

`activate_devstill.sh` is a checkout-relative convenience helper after `.venv` has
been created. The legacy `setup_wsl_cpu.sh` remains as a compatibility wrapper for
`./setup.sh --device cpu`.
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
The environment uses:
```text
Python 3.12
CPU or NVIDIA CUDA PyTorch
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
PyTorch and CUDA build versions
requested and resolved embedding device
CUDA availability and GPU name
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
    device="cpu",  # or "cuda", selected by embedding.device
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
    "device": "auto",
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
  "retrieval": {
    "exclude": []
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
  },
  "executor": {
    "enabled": true,
    "backend": "pi",
    "timeout_seconds": 300,
    "context_window": "auto",
    "reasoning": "off",
    "max_output_tokens": 4096,
    "max_tool_calls": 12,
    "validation_commands": []
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

`retrieval.exclude` is a separate, reversible “do not use for answers or plans”
list. Repository-relative POSIX paths and globs remain indexed but are filtered at
query time from search, context, Plan, Deep Plan, refinements, and replacements.
Manage it without reindexing:

```bash
devstill retrieval-ignore
devstill retrieval-ignore add "vendor/**"
devstill retrieval-ignore add src/obsolete.py
devstill retrieval-ignore remove src/obsolete.py
```
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
device: auto
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
The agent is not a second model bundled with Devstill: it is the connection from
Devstill to an LLM server that you start or obtain separately. The server must
provide `POST /v1/chat/completions`. Indexing, search, and context assembly work
without this agent; `plan` and `deep-plan` require it.

Endpoint and model configuration are repository-local. API tokens are never written
to `devstill.json` or displayed. The interactive TUI can remember a token in
`~/.config/devstill/credentials.json`, an atomic user-only file with `0600`
permissions. The configured environment variable remains supported and takes
precedence over a remembered token. Embedded URL credentials are rejected.

Plan and Deep Plan use a separate 6,144-token output ceiling; this is a maximum, so
models can stop earlier. General agent calls retain the smaller LLM
`max_output_tokens` limit, while Pi execution uses
`executor.max_output_tokens` and defaults to 4,096 tokens. Pi resolves its context
from `/v1/models` when possible, including llama.cpp's `meta.n_ctx`, and otherwise
uses a conservative 8,192-token fallback. Its output cap is never more than one-third
of the resolved context. Planning requests
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
# First inspect the setup help and examples.
devstill help agent configure

# Example: download a model into Ollama, then configure its running server.
ollama pull qwen2.5-coder:7b
devstill agent configure \
  --base-url http://127.0.0.1:11434/v1 \
  --model qwen2.5-coder:7b \
  --planning-max-output-tokens 6144 \
  --planning-reasoning off
devstill agent
devstill agent test
```
`--base-url` is the API root ending in `/v1`, not the full
`/v1/chat/completions` URL. `--model` is not a placeholder for the word `MODEL`;
use the identifier shown by your server. When supported, this lists the available
identifiers:
```bash
curl http://127.0.0.1:11434/v1/models
```

Common local-server examples:
```bash
# Ollama (ensure the Ollama app/service is running)
ollama pull qwen2.5-coder:7b
devstill agent configure \
  --base-url http://127.0.0.1:11434/v1 \
  --model qwen2.5-coder:7b
```
For LM Studio, the common base URL is `http://127.0.0.1:1234/v1`; load a model,
start its local server, and copy its displayed model identifier into `--model`. For
llama.cpp, the common base URL is `http://127.0.0.1:8080/v1`; pass the model
identifier configured for that server. Do not type the literal words `URL`, `MODEL`,
or `YOUR_MODEL` from a usage message.

Local servers commonly accept requests without a key. If yours requires one, set
the default environment variable before testing:
```bash
export DEVSTILL_AGENT_API_KEY="your-actual-key"
devstill agent test
```
To use a different variable name, pass `--api-key-env NAME` during configuration and
export that name instead. Devstill reads the variable when it sends a request; the
secret is not saved in `devstill.json`.

Running `devstill agent configure` without options prints this setup help. Standard
help forms also work: `devstill --help`, `devstill agent --help`, and
`devstill agent configure --help`.

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
repository context files, a bounded tool budget, and strict Plan-only tools. It can
read only approved existing files, edit or write only labels that allow it, delete
only an exact `Delete` target. Devstill checks the staged file contracts before
producing a patch preview.
Discovery tools (`ls`, `grep`, and `find`) and the shell are unavailable.

Blue `Read` targets are copied for reference but cannot be changed. Existing green
`Add`, orange `Remove`, and purple `Modify` targets may change but must remain files.
Black `Delete` targets must be removed completely, while pink `New` targets must
create one exact safe repository-relative file. `New` cannot name a directory;
parent directories are created automatically. Devstill rejects symlinks,
traversal, unlisted files, and any read-only change before writing
`.devstill/.execute.patch`.

If Pi completes part of a Plan but stops early, Devstill makes one fresh, focused
repair attempt containing only the unmet targets. Zero-progress runs and provider
errors are not retried. Both attempts remain visible in the Pi event log.

`Check staged changes against Plan` is an automatic file-contract check, not a
request for user approval. Verification does not consume the read/edit tool-call
budget. A successful check ends the run immediately; reaching the tool budget or
repeating a failed check without edits also stops Pi so Devstill can inspect the
staged changes and use the bounded repair flow if needed. These stops do not bypass
file-contract validation or automatically apply a patch. The CLI's `--yes` option
opts into applying a valid finished patch; the TUI uses its **Apply patch** button.

The patch is displayed before application. Interactive CLI and TUI use explicit
confirmation; `--dry-run` only generates the preview and `--yes` is required for
noninteractive application. Devstill rechecks original file hashes and runs
`git apply --check` immediately before applying. It never commits or pushes.

After a successful `/plan` or `/deep-plan`, its card becomes a live review workspace.
The colored status on each existing file is an inline dropdown for `Read`, `Add`,
`Remove`, `Modify`, or `Delete`. A red `×` removes a row for this handoff, a blue `↻`
replaces an existing file with the next eligible hybrid result, and a confirmed red
`!` permanently adds that exact path to `retrieval.exclude`. `New` and unresolved
`Search` rows only offer `×`. Reasons use a white, high-contrast editor while focused
and save on Enter or focus loss. Filenames and reasons occupy aligned columns, with
reasons wrapping and growing to show their full text, including while editing.
In cards narrower than 100 terminal cells, reasons appear beneath the filename
heading with a two-cell indent. Reasons retain their 240-character limit; wrapping
is visual and does not insert line breaks into the saved plan.

The `?` control on an existing file opens a question dialog and immediately asks
the configured agent to review the complete file against the current Plan or Deep
Plan. Its initial review explains the file's role and checks whether the impact
label and reason match the original request and accepted refinements, highlighting
mismatches when needed. The request includes every plan item's path, label, and
reason, plus the full source of the selected file. Other files' source is not read.

Type a question while that review runs. `Enter` sends it, or queues one question
behind the current response; `Shift+Enter` adds a line. Follow-up answers retain
the complete file, plan snapshot, and successful conversation turns. Errors stay
inside the dialog with a **Retry** button. **Close** or `Escape` returns to the plan;
late replies are ignored. Reopening starts a fresh review of the latest file and
plan. The discussion does not change labels, reasons, files, or trigger Pi.

`New` and unresolved `Search` entries have a disabled `?` because there is no
existing file to read. Missing, excluded, binary, or unreadable files produce an
error before contacting the agent. Source is never silently truncated; if the
full file and conversation exceed the endpoint's context window, the request error
is shown. File questions use the configured general output-token limit and planning
reasoning setting, with the existing agent diagnostics in `.devstill/logs/agent.jsonl`.

An **Add another file** form stays visible throughout Plan review. Type any part of
a repository path to get substring and fuzzy matches, use Up/Down and Enter to pick
one, or press `Ctrl+P` to focus the picker from anywhere in the review. Exact paths
still work for files that are not indexed. The action dropdown sits to the left of
the filename search, matching the existing Plan rows. Add an optional reason; a safe
default is supplied when the reason is blank. The form clears and remains focused
after each addition so several files can be added in a row. Missing safe paths become
fixed pink `New` rows. Manually adding a temporarily suppressed existing path
restores and pins it; permanently excluded paths must be unbanned first. Enter in the
bottom composer submits a follow-up refinement and keeps its compact history on the
same card. Manual edits stay pinned while untouched retrieval rows may change. Pi
starts only through **Preview with Pi**, which is disabled for an unsent draft,
empty plan, unresolved `Search`, or active review work. Every accepted mutation is
saved immediately.

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
  "context_window": "auto",
  "reasoning": "off",
  "max_output_tokens": 4096,
  "max_tool_calls": 12,
  "validation_commands": ["npm run typecheck", "python3 -m pytest -q"]
}
```
Override auto-detection for a server that does not advertise context metadata:
```bash
devstill agent configure --execution-context-window 12288 \
  --execution-max-output-tokens 4096 --execution-reasoning off
```
`devstill agent` and `devstill doctor` show requested and resolved execution limits.
Only these user-configured commands run after application. A validation failure
leaves the reviewed changes in place and is recorded in `.execute.json`; Pi events
are written to `.devstill/logs/pi-execute.jsonl` without API credentials.
Venv rule
Keep all Devstill client-side functionality in the single dedicated venv:
```text
Textual TUI
parsers
CPU or CUDA embedding model
Chroma
SQLite
fuzzy search
indexer
search engine
LLM HTTP client
```
Do not install Devstill into each scanned repository's environment.
A separate Python/CUDA LLM server should still use its own venv or container.
The following already run as separate services and do not need a Devstill-specific venv:
llama.cpp server
Ollama
LM Studio
Current commands
```bash
devstill
devstill help
devstill help agent configure
devstill init
devstill init --force
devstill remove
devstill remove --dry-run
devstill remove --index-only
devstill remove --yes
devstill doctor
devstill doctor --load-model
devstill device
devstill device auto
devstill device cpu
devstill device cuda
devstill update --check
devstill update
devstill update --yes
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

The manual index command shows live discovery, file-checking, parsing, embedding,
and persistence progress on stderr, including checked/changed/unchanged/parsed file
counts. The optional `optimum`/ONNX warning from the embedding model is suppressed;
ONNX support is not used by Devstill's normal embedding workflow.

Task Review and YOLO

Both modes use the existing Quick engine: local hybrid retrieval, compact
file-impact classification, then scoped Pi execution. There is no generated task
breakdown, child-task plan or separate structured-edit prompt on this path.

- **Task Review** shows Read/Add/Remove/Modify/Delete/New file impacts. **Run task**
  approves coding and application together, without a second patch approval.
- **YOLO** uses the same engine and scope checks, skipping the review stop.
- **Search** performs local lookup without edits or a model call.

The TUI starts in Task Review. The mode selector applies to ordinary text;
`/task`, `/yolo` and `/search` explicitly choose a mode for that request. YOLO is
never saved as the next session's default. **Edit request** returns a review to
the composer; **Cancel** keeps its artifacts without running it.

```bash
devstill task "Change the submit button from green to blue"
devstill task "Change the submit button from green to blue" --yolo
devstill task "Make the header parser reject blank names" --json
devstill task --run RUN_ID --yolo
```

Interactive CLI Task Review asks once whether to run the displayed task.
With redirected stdin or `--json`, it saves the review without executing it;
use the printed run ID to execute later. `--yolo --json` returns the execution
result and exits nonzero on failure. Started runs cannot be replayed automatically.

Quick retrieves 12 candidates and packs up to five chunks with the original
adaptive selection and token budgeting. Tests are included when the request asks
for them. The original file-impact classifier and its bounded format repair are
unchanged. Task IDs, display metadata and run state are created locally; they
require no extra generation. The original request and file impacts go directly to
`RepositoryExecutor`, using the existing Pi prompts, tools and limits. Pi can
make multiple bounded tool/model calls and one focused repair for a partial
change; this is not a promise of a single model response per edit.

Configure checks using the existing executor setting:

```json
{
  "executor": {
    "validation_commands": ["python -m pytest -q"]
  }
}
```

Only configured commands execute. Without checks the result is
`applied_unvalidated`; `validated` means those checks passed. A failed check
reports failure and leaves the applied patch available for inspection. Closing
the TUI signals cancellation before application; already applied changes remain.

Reviews, source hashes, selected evidence, model logs, patches, checks and run
status live under `.devstill/tasks/RUN_ID/`. The original Plan/Pi artifacts are
stored in its `quick/` subdirectory, so a task does not overwrite a separate
`/plan` review. Source changes since retrieval/review stop application, and a
successful write requests incremental indexing before the next task retrieval.
Unresolved Search entries block execution. The existing Pi scope checks still
reject unapproved writes and enforce the reviewed file impacts.

The experimental dependency scheduler and structured-edit engine remain readable
for older saved drafts (`engine: structured`, also the default for drafts without
an engine field). New Task Review/YOLO drafts use `engine: quick`.
`task_concurrency` and `task_validation_commands` apply only to that older
experimental engine; Quick uses `validation_commands`. Feature coordination is available only when explicitly selected; Quick remains
the default. See
[the Quick restoration and small-edit comparison](archive/benchmarks/retrieval/QUICK_RESTORE_RESULTS.md)
and [the historical failed Task comparison](archive/benchmarks/retrieval/TASK_V2_RESULTS.md).

Choose **Feature** beside the Review/YOLO selector for multiple Quick tasks.
`/feature` tries the original request through Quick first; `/feature-yolo` runs
without a review stop. A runnable review goes straight to Quick execution. Only an
unresolved review invokes coordination, capped at three tasks. CLI equivalents are `devstill feature "request" [--yolo]` and
`devstill feature --run FEATURE_ID --yolo`. Quick remains the startup default.
Features run serially, refresh context between children, and stop on failed
checks, stale inputs or scope conflicts. **Stop feature** prevents further work;
applied changes remain. See [the implementation and benchmark handoff](archive/benchmarks/retrieval/FEATURE_IMPLEMENTATION.md)
for validation settings, limits and the prepared comparison commands. The [live feature comparison](archive/benchmarks/retrieval/FEATURE_COORDINATION_RESULTS.md)
shows progress, but feature mode still trails Quick on whole-feature acceptance.

Interactive TUI

Run `devstill` from anywhere beneath an initialized repository. The prompt accepts
ordinary task requests in the selected mode and slash commands:

```text
Change the submit button from green to blue
/task add account activation
/yolo update the footer copyright
/search registration flow
/context explain the login session
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
commands and also prefills the prompt. Search runs locally; `/task`, `/yolo`
and `/agent test` contact the configured endpoint. Task Review shows file impacts
inline, with Run task, Edit request and Cancel actions below the composer.

The legacy `/plan`, `/deep-plan` and `/execute` commands remain accepted for
compatibility, but are no longer primary sidebar actions. Their behavior is
unchanged: Plan and Deep Plan open an iterative Pi handoff. Enter submits a follow-up
refinement and **Preview with Pi** is the only execution trigger. The original query
and all accepted refinements are persisted and sent to Pi. Pi
handoff, label review, Search resolution, and patch approval stay inline: Plan
statuses are clickable dropdowns in the feed and action buttons appear at the
bottom-right of the composer. `/agent configure` remains a separate setup form.

Human-readable Search, Context, Plan, Deep Plan, index, and execution results use a
compact layout. Interactive Plan and Deep Plan review cards align labels, filenames,
and wrapping reasons in columns, stacking reasons below filenames in narrow cards.
CLI and Markdown Plan items keep their reason immediately below the label and path,
without a blank spacer row. Decorative blank rows are omitted,
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
the incremental-index and quality summary. Embedding is grouped for structured
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
model and raises Devstill's memory use by roughly 1.1 GiB, but the first search,
context, or plan then avoids that cold-start delay. The sidebar reports the encoder
as `cold`, `loading`, or `warm` alongside its resolved CPU/CUDA device. The same
encoder and Chroma client are reused for the rest of that TUI session, while each
command keeps its own lightweight SQLite
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
`Read`, `Add`, `Remove`, `Modify`, `Delete`, `Search`, and `New` items with one short
reason.
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
one embedding batch. A failed initial pass preserves every previous Deep Plan artifact. A
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

Maintainers publish the updater channel by bumping `project.version`, pushing a
matching stable `vMAJOR.MINOR.PATCH` tag, and allowing the release workflow to pass
the CPU test suite before it creates the GitHub Release. CUDA smoke testing is a
manual, hardware-gated CI job for a self-hosted NVIDIA runner.
See `ToDo.md` for the implementation sequence and acceptance criteria.
