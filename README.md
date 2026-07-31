devstill
`devstill` is a standalone, local-first repository-intelligence TUI for developers and AI coding agents.
Run `devstill` from any directory inside a configured codebase. It searches upward through parent directories until it finds a `devstill.json`, then opens the TUI for that repository.
The long-term goal is to make `devstill` the fastest and most useful way for a human or local AI agent to understand, search, and navigate a Git repository without sending source code to an external service.
Project goals
`devstill` should provide several complementary search modes:
Exact symbol lookup for known functions, classes, methods, fields, and paths
Fuzzy lookup for partially remembered symbol names
Full-text and lexical search for exact code phrases
Semantic vector search for natural-language questions
Relationship traversal between routes, views, models, functions, imports, callers, and tests
LLM-ready context assembly that returns only relevant source chunks
Optional local-LLM enrichment for labels, summaries, and ambiguous chunk decisions
Embeddings are only one part of the system. The intended architecture combines deterministic parsing, exact indexes, lexical search, vector search, and code relationships.
Current status
The current starter implements:
Global `devstill` console command
Dedicated Python virtual environment
CPU-only PyTorch installation
Local `jina-embeddings-v2-base-code` model
ChromaDB dependency and health check
`devstill init`
`devstill doctor`
`devstill doctor --load-model`
Git-aware `devstill files`
Deterministic Python and JavaScript-family `devstill parse`
Incremental `devstill index`
Canonical SQLite and FTS5 storage
Persistent Chroma vector storage
Hybrid `devstill search`
Compact Markdown and JSON `devstill context`
Upward discovery of `devstill.json`
Initial Textual TUI shell
Per-repository configuration
Shared global embedding model
Per-repository `.devstill/` data directory
The following major features are not implemented yet:
Search screens in the TUI
Code relationship graph
Local-LLM enrichment
Token-budgeted and relationship-aware context expansion
Tree-sitter structural parsing for HTML, CSS, SQL, and additional languages
File watching
Running `devstill` still opens the starter TUI shell. Use the CLI for indexing and search until TUI integration is implemented.
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
"tree-sitter-javascript>=0.25,<0.26"
"tree-sitter-typescript>=0.23.2,<0.24"
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
    "collection": "devstill_code"
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
    "include": [
      "**/*.py",
      "**/*.js",
      "**/*.jsx",
      "**/*.ts",
      "**/*.tsx",
      "**/*.json",
      "**/*.toml",
      "**/*.yaml",
      "**/*.yml",
      "**/*.sql",
      "**/*.html",
      "**/*.css",
      "**/*.md"
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
    "model": ""
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
Python uses deterministic `ast` parsing. JavaScript, JSX, TypeScript, and TSX
use their corresponding Tree-sitter grammars.
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
├── cache/
├── logs/
└── manifests/
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
The local LLM will be called through an HTTP API later.
Its responsibilities should include:
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
No LLM work is required for the first working indexer.
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
future LLM HTTP client
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
devstill doctor
devstill doctor --load-model
devstill files
devstill files --count
devstill files --json
devstill parse path/to/file.py
devstill parse path/to/file.py --json
devstill parse frontend/src/App.tsx
devstill parse frontend/src/App.tsx --json
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
devstill --version
```
`devstill context` returns five records by default. Every successful context command
also overwrites `.devstill/.context.md` and `.devstill/.context.json` with both
reviewable formats, regardless of which format is printed to stdout.

Parser versions are tracked per language family. After installing a parser
upgrade, a normal `devstill index` reparses only files whose parser changed;
`--rebuild` is not required.

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
