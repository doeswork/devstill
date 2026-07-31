We need to include other major coding laguanges like rust, ruby, go, sql, etc to the parsing or Structural Chunking and have some generic fallback if we don't have the laguange. Or if a chunk is over 1000 lines of code. Like maybe a ineractive "devstill index --verbose" that lets us know how many good files, meh files and bad files

## Current implementation status

- JavaScript, JSX, TypeScript, and TSX structural parsing is implemented with Tree-sitter.
- Exact functions, classes, methods, named handlers, callable assignments, imports, types, interfaces, enums, and top-level executable expressions receive canonical chunks.
- Whole JavaScript-family modules remain canonical but are excluded from embeddings.
- Anonymous callbacks remain inside their named owner to avoid noisy search records.
- Parser versions are tracked per language family so existing indexes update only affected files.
- HTML, CSS, SQL, Rust, Ruby, Go, large-file quality reporting, and structural fallback policy remain pending.
