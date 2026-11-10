"""Linked dependency directories (node_modules) in JS mode.

A workspace's node_modules is a symlink to the installed (often shared)
dependency tree, so a command that writes through it damages the install
for every later run. Agent commands that would write there are refused
before they run (`dependency_write`); as a backstop, commands that mention
node_modules or a package manager are bracketed by a cheap manifest of the
linked tree (`dependency_manifest`), and any change is reported.
"""

from __future__ import annotations

import fnmatch
import os
import posixpath
import re
import shlex
import time
from pathlib import Path, PurePosixPath

PACKAGE_MANAGERS = {"npm", "pnpm", "yarn", "bun"}
# Package-manager subcommands that add, remove or rewrite installed packages.
PACKAGE_MUTATIONS = {
    "install",
    "i",
    "isntall",
    "add",
    "remove",
    "rm",
    "r",
    "uninstall",
    "un",
    "unlink",
    "update",
    "up",
    "upgrade",
    "ci",
    "link",
    "ln",
    "dedupe",
    "ddp",
    "prune",
    "rebuild",
    "rb",
    "reinstall",
    "patch-commit",
    "patch-remove",
}
# Package-manager options before the subcommand that take a value
# (`pnpm --filter x add y`, `npm -w a install`, `yarn --cwd x add y`).
PACKAGE_VALUE_OPTIONS = {
    "npm": {"-w", "--workspace", "--prefix", "-C", "--loglevel", "--registry"},
    "pnpm": {"--filter", "-F", "-C", "--dir", "--loglevel", "--reporter"},
    "yarn": {"--cwd", "--registry", "--cache-folder", "--modules-folder"},
    "bun": {"--cwd", "-c", "--config", "--registry"},
}
# Commands that write to (or delete) every path argument they get.
WRITE_ANY = {
    "rm",
    "rmdir",
    "unlink",
    "mv",
    "chmod",
    "chown",
    "chgrp",
    "touch",
    "truncate",
    "mkdir",
    "shred",
    "tee",
    "patch",
    "ed",
    "ex",
}
# Commands that write only to their destination (the last path argument,
# or -t/--target-directory).
WRITE_DESTINATION = {"cp", "ln", "install", "rsync"}
# Leading words that run the next word as the command, and their options
# that take a value (`nice -n 5 rm ...`).
WRAPPERS = {
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-U", "-r", "-t", "--user"},
    "env": {"-u", "-C", "-S", "--unset", "--chdir", "--split-string"},
    "command": set(),
    "exec": {"-a"},
    "nice": {"-n", "--adjustment"},
    "nohup": set(),
    "time": {"-f", "-o", "--format", "--output"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "xvfb-run": set(),
}
# Package runners that may run a package manager (`npx pnpm install`).
RUNNERS = {"npx", "bunx", "pnpx", "corepack"}
# Shells whose -c argument is a command line of its own.
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
# Shell words that start a compound command: the command follows them.
RESERVED = {"do", "then", "else", "elif", "if", "while", "until", "!", "{", "}"}
# xargs options that take a value as the next word.
XARGS_VALUE_OPTIONS = {"-a", "-d", "-E", "-I", "-L", "-n", "-P", "-s"}
SEPARATORS = {";", "&&", "||", "|", "&", "|&", "(", ")", "\n", ";;", "{", "}"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>", "<>"}
MENTIONS_TOOLS = re.compile(r"\b(?:npm|pnpm|yarn|bun|npx|bunx)\b")
GLOB = re.compile(r"[*?\[]")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# find's escaped parentheses, kept as words (not subshell separators).
_FIND_GROUP = {"\\(": " __devstill_lp__ ", "\\)": " __devstill_rp__ "}
# Top-level entries of an install that are its content; other dot entries
# are tool caches (.vite, .cache, ...) that test runs create.
INSTALL_METADATA = {
    ".pnpm",
    ".bin",
    ".modules.yaml",
    ".package-lock.json",
    ".yarn-state.yml",
    ".yarn-integrity",
}


def _strip_comments(command):
    """`command` without shell comments (a # that starts a word, outside
    quotes, up to the end of its line)."""
    out, quote, index, previous = [], None, 0, " "
    while index < len(command):
        char = command[index]
        if quote:
            out.append(char)
            if char == "\\" and quote == '"' and index + 1 < len(command):
                out.append(command[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
        elif char == "\\" and index + 1 < len(command):
            out.append(command[index : index + 2])
            index += 2
            previous = "x"
            continue
        elif char in "'\"":
            quote = char
            out.append(char)
        elif char == "#" and previous in " \t\r\n;&|()":
            end = command.find("\n", index)
            if end == -1:
                break
            index = end
            continue
        else:
            out.append(char)
        previous = char
        index += 1
    return "".join(out)


def _tokens(command):
    command = _strip_comments(command)
    for escaped, word in _FIND_GROUP.items():
        command = command.replace(escaped, word)
    lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:  # unbalanced quotes: a rough split still finds paths
        return re.findall(r"[^\s;&|()<>]+|[;&|()<>\n]+", command)


def _simple_commands(tokens):
    """Split tokens into simple commands; redirections stay with their command."""
    current = []
    for token in tokens:
        if token in SEPARATORS or token.startswith(("(", ")")):
            if current:
                yield current
            current = []
            continue
        current.append(token)
    if current:
        yield current


def _expand_braces(word, limit=64):
    """Brace expansion of one word ({a,b} -> a, b), as the shell does."""
    match = re.search(r"\{([^{}]*,[^{}]*)\}", word)
    if not match:
        return [word]
    out = []
    for part in match.group(1).split(","):
        out.extend(_expand_braces(word[: match.start()] + part + word[match.end() :]))
        if len(out) >= limit:
            break
    return out[:limit]


def _computed(word):
    """An operand whose value the text does not show ($var, $(...), `...`)."""
    return "$" in word or "`" in word


class DependencyPaths:
    """Recognizes paths into the linked dependency directories."""

    def __init__(self, root, names=("node_modules",)):
        self.root = Path(root)
        self.names = tuple(names)
        self.resolved = []
        for name in self.names:
            link = self.root / name
            try:
                if link.exists():
                    self.resolved.append(str(link.resolve()))
            except OSError:
                continue

    def _names(self, text):
        """`text` names a path in a dependency directory itself."""
        parts = PurePosixPath(text).parts
        if any(
            part == name or (GLOB.search(part) and fnmatch.fnmatchcase(name, part))
            for part in parts
            for name in self.names
        ):
            return True
        return any(
            text == real or text.startswith(real.rstrip("/") + "/")
            for real in self.resolved
        )

    def inside(self, path, cwd=""):
        """`path` (a command argument, relative to the directory `cwd` the
        command moved into, "" for the repository root) points into a
        dependency directory."""
        text = str(path).strip()
        if not text or text in ("-", "/dev/null") or text.startswith("/dev/"):
            return False
        if self._names(text):
            return True
        if cwd and not text.startswith(("/", "~")):
            if self._names(posixpath.normpath(posixpath.join(cwd, text))):
                return True
            # Inside the install, `..` follows its real location, not the
            # link the command came through: treat it as still inside.
            return self._names(cwd) and ".." in PurePosixPath(text).parts
        return False

    def ancestor(self, path, cwd=""):
        """`path` is a directory that holds a dependency directory (the
        repository root, say), so a search following links reaches it."""
        text = str(path).strip() or "."
        if text.startswith("~") or _computed(text):
            return False
        joined = posixpath.normpath(
            text if text.startswith("/") else posixpath.join(cwd or ".", text)
        )
        base = Path(joined) if joined.startswith("/") else self.root / joined
        return any((base / name).exists() for name in self.names)

    def mentioned(self, command):
        return any(name in command for name in self.names) or any(
            real in command for real in self.resolved
        )


def _arguments(words):
    """Non-option arguments (options and -- dropped)."""
    out, ended = [], False
    for word in words:
        if not ended and word == "--":
            ended = True
            continue
        if not ended and word.startswith("-") and word != "-":
            continue
        out.append(word)
    return out


def _package_manager(word):
    base = PurePosixPath(word).name
    base = base.split("@", 1)[0] if not base.startswith("@") else base
    return base if base in PACKAGE_MANAGERS else None


def _command_word(words):
    """(command, its arguments) after assignments, wrapper commands and
    package runners that start a package manager."""
    index = 0
    while index < len(words):
        word = words[index]
        if ASSIGNMENT.match(word):
            index += 1
            continue
        base = PurePosixPath(word).name
        if base in WRAPPERS:
            values = WRAPPERS[base]
            index += 1
            # options (and env assignments) before the command
            while index < len(words) and (
                words[index].startswith("-")
                or (base == "env" and ASSIGNMENT.match(words[index]))
            ):
                index += 2 if words[index] in values else 1
            if base == "timeout":
                index += 1  # the duration
            continue
        if base in RUNNERS:
            ahead = index + 1
            while ahead < len(words) and words[ahead].startswith("-"):
                ahead += 2 if words[ahead] in ("-p", "--package", "-c") else 1
            if ahead < len(words) and _package_manager(words[ahead]):
                index = ahead
                continue
        manager = _package_manager(word)
        return manager or base, words[index + 1 :]
    return None, []


def _in_place(command, arguments):
    if command == "sed":
        return any(
            a == "--in-place"
            or a.startswith("--in-place=")
            or re.match(r"^-[a-zA-Z]*i", a)
            for a in arguments
            if a.startswith("-")
        )
    if command in ("perl", "ruby"):
        return any(re.match(r"^-[a-zA-Z0-9]*i", a) for a in arguments)
    return False


def _package_mutation(name, arguments):
    """`npm install`-like: the subcommand after global options changes the
    installed packages (help output never does)."""
    if "--help" in arguments or "-h" in arguments:
        return None

    values = PACKAGE_VALUE_OPTIONS[name]

    def subcommand(words):
        index = 0
        while index < len(words) and words[index].startswith("-"):
            index += 2 if words[index] in values else 1
        return words[index:]

    rest = subcommand(list(arguments))
    if name == "yarn" and rest[:1] == ["workspace"]:
        rest = subcommand(rest[2:])  # yarn workspace <name> <command>
    elif name == "yarn" and rest[:2] == ["workspaces", "foreach"]:
        rest = subcommand(rest[2:])
    sub = rest[0] if rest else ("install" if name == "yarn" else "")
    if sub in PACKAGE_MUTATIONS or (sub == "audit" and "fix" in rest):
        return f"`{name} {sub}` changes the installed packages"
    return None


def _find_parts(arguments):
    """(start paths, expression, follows links) of a find command."""
    index, follow = 0, False
    while index < len(arguments) and (
        arguments[index] in ("-H", "-L", "-P", "-D") or arguments[index][:2] == "-O"
    ):
        follow = follow or arguments[index] == "-L"
        index += 2 if arguments[index] == "-D" else 1
    starts = []
    while index < len(arguments) and not (
        arguments[index].startswith(("-", "(", "!", ")"))
        or arguments[index].startswith("__devstill_")
    ):
        starts.append(arguments[index])
        index += 1
    rest = arguments[index:]
    return starts or ["."], rest, follow or "-follow" in rest


def _shell_script(arguments):
    """The command line a shell runs with -c (None: a script file or stdin)."""
    for index, word in enumerate(arguments):
        if not word.startswith("-"):
            return None
        if word == "-c" or (not word.startswith("--") and "c" in word[1:]):
            return arguments[index + 1] if index + 1 < len(arguments) else None
    return None


def _walk(command, paths, cwd):
    """(command, arguments, cwd, redirection target into a dependency
    directory or None) for each simple command; cd/pushd/popd move cwd."""
    previous, stack = cwd, []
    for words in _simple_commands(_tokens(command)):
        plain, redirected, skip = [], None, False
        for index, word in enumerate(words):
            if skip:
                skip = False
                continue
            if word in REDIRECTS or (
                word.endswith((">", ">>")) and word.rstrip(">").isdigit()
            ):
                target = words[index + 1] if index + 1 < len(words) else ""
                skip = True
                if redirected is None and paths.inside(target, cwd):
                    redirected = target
                continue
            if word in ("<", "<<", "<<<", ">&", "<&"):
                skip = True
                continue
            plain.append(word)
        while plain and plain[0] in RESERVED:
            plain = plain[1:]
        name, arguments = _command_word(plain)
        if name in ("cd", "pushd", "popd"):
            values = _arguments(arguments)
            if name == "popd":
                cwd = stack.pop() if stack else "~"
                continue
            target = values[0] if values else "~"
            if target == "-":
                cwd, previous = previous, cwd
                continue
            if name == "pushd":
                stack.append(cwd)
            previous = cwd
            if target.startswith(("/", "~")) or _computed(target):
                cwd = target
            else:
                cwd = posixpath.normpath(posixpath.join(cwd or ".", target))
            if redirected is None:
                continue
        yield name, arguments, cwd, redirected


def _reads_dependencies(name, arguments, paths, cwd):
    """The command takes a path in a dependency directory as an operand
    (its output may name files there: `ls node_modules | xargs rm`)."""
    if name is None:
        return False
    if name == "find":
        starts, _, follow = _find_parts(arguments)
        return any(
            paths.inside(s, cwd) or (follow and paths.ancestor(s, cwd)) for s in starts
        )
    return any(
        paths.inside(v, cwd)
        for word in _arguments(arguments)
        for v in _expand_braces(word)
    )


def _writes(name, arguments, paths, cwd, sources, depth):
    """How the simple command `name arguments` would write into a
    dependency directory, or None. `sources`: the command line also reads
    paths from there (a computed operand may be one of them)."""
    if "--help" in arguments:
        return None
    if name in SHELLS:
        script = _shell_script(arguments)
        return _scan(script, paths, cwd, depth + 1, sources) if script else None
    if name == "eval":
        return _scan(" ".join(arguments), paths, cwd, depth + 1, sources)
    if name in PACKAGE_MANAGERS:
        return _package_mutation(name, arguments)
    values = [v for word in _arguments(arguments) for v in _expand_braces(word)]

    def target(candidates):
        hit = next((v for v in candidates if paths.inside(v, cwd)), None)
        if hit is None and sources and any(_computed(v) for v in candidates):
            hit = "a path computed from node_modules"
        return hit

    if name in WRITE_ANY:
        hit = target(values)
        return f"`{name}` on {hit}" if hit is not None else None
    if name in WRITE_DESTINATION:
        destination = None
        for index, word in enumerate(arguments):
            if word in ("-t", "--target-directory") and index + 1 < len(arguments):
                destination = arguments[index + 1]
            elif word.startswith("--target-directory="):
                destination = word.split("=", 1)[1]
        if destination is None and len(values) >= 2:
            destination = values[-1]
        if destination is not None and target([destination]) is not None:
            return f"`{name}` into {destination}"
        return None
    if name == "dd":
        hit = target([v[3:] for v in values if v.startswith("of=")])
        return f"`dd of={hit}`" if hit is not None else None
    if _in_place(name, arguments):
        hit = target(values)
        return f"`{name}` editing {hit} in place" if hit is not None else None
    if name == "find":
        starts, rest, follow = _find_parts(arguments)
        if not any(
            paths.inside(s, cwd) or (follow and paths.ancestor(s, cwd)) for s in starts
        ):
            return None
        if "-delete" in rest:
            return "`find -delete` in node_modules"
        for index, word in enumerate(rest):
            if word in ("-exec", "-execdir", "-ok", "-okdir"):
                inner, inner_arguments = _command_word(rest[index + 1 :])
                if _writer(inner, inner_arguments):
                    return f"`find -exec {inner}` in node_modules"
        return None
    if name == "xargs":
        index = 0
        while index < len(arguments) and arguments[index].startswith("-"):
            if arguments[index] == "--":
                index += 1
                break
            index += 2 if arguments[index] in XARGS_VALUE_OPTIONS else 1
        inner, inner_arguments = _command_word(arguments[index:])
        if inner is None:
            return None
        if sources and _writer(inner, inner_arguments):
            return f"`xargs {inner}` on paths from node_modules"
        return _writes(inner, inner_arguments, paths, cwd, sources, depth + 1)
    return None


def _writer(name, arguments):
    """The command writes to the paths it is given (find -exec, xargs)."""
    return bool(name) and (
        name in WRITE_ANY
        or name in WRITE_DESTINATION
        or name in SHELLS
        or _in_place(name, arguments)
    )


def _scan(command, paths, cwd, depth, sources=False):
    if depth > 4 or not command:
        return None
    walked = list(_walk(command, paths, cwd))
    sources = sources or any(
        _reads_dependencies(name, arguments, paths, at)
        for name, arguments, at, _ in walked
    )
    for name, arguments, at, redirected in walked:
        if redirected is not None:
            return f"output redirection into {redirected}"
        if name is None:
            continue
        reason = _writes(name, arguments, paths, at, sources, depth)
        if reason:
            return reason
    return None


def dependency_write(command, paths: DependencyPaths):
    """A short description of how `command` would modify a dependency
    directory (None when it would not, as far as the text shows).

    Static reading of the command line: simple commands (wrappers, package
    runners, `sh -c`, `eval`, xargs and find -exec followed), the directory
    `cd` moved into, brace expansion, and operands the text does not show
    ($var, $(...)) when the line also reads paths from node_modules."""
    return _scan(command, paths, "", 0)


def refusal_text(reason):
    return (
        f"Refused: this command would modify the installed dependencies ({reason}). "
        "node_modules is a shared install: never modify, delete or reinstall "
        "anything in it (reading is fine). Make the change in the repository's own "
        "files; nothing was run."
    )


def mentions_dependencies(command, paths: DependencyPaths):
    """The command names a dependency directory or runs a package manager."""
    return paths.mentioned(command) or MENTIONS_TOOLS.search(command) is not None


def dependency_manifest(directories, seconds=1.5, limit=300_000):
    """{name: (size, mtime_ns, link target)} of the files in the resolved
    dependency directories (top-level tool caches skipped), or None when
    the tree is too large to list within `seconds`/`limit` entries."""
    found = {}
    started = time.perf_counter()
    for label, directory in directories:
        pending = [(str(directory), "")]
        while pending:
            folder, prefix = pending.pop()
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            for entry in entries:
                if (
                    not prefix
                    and entry.name.startswith(".")
                    and entry.name not in INSTALL_METADATA
                ):
                    continue
                name = f"{label}/{prefix}{entry.name}"
                try:
                    if entry.is_symlink():
                        found[name] = (0, 0, os.readlink(entry.path))
                    elif entry.is_dir(follow_symlinks=False):
                        pending.append((entry.path, prefix + entry.name + "/"))
                    else:
                        info = entry.stat(follow_symlinks=False)
                        found[name] = (info.st_size, info.st_mtime_ns, None)
                except OSError:
                    continue
            if len(found) > limit or time.perf_counter() - started > seconds:
                return None
    return found


def manifest_changes(before, after):
    """Sorted (kind, name) pairs: 'removed', 'added' or 'changed'."""
    changes = []
    for name in sorted(set(before) | set(after)):
        if name not in after:
            changes.append(("removed", name))
        elif name not in before:
            changes.append(("added", name))
        elif before[name] != after[name]:
            changes.append(("changed", name))
    return changes


def changes_text(changes, limit=6):
    shown = "; ".join(f"{kind} {name}" for kind, name in changes[:limit])
    more = f" and {len(changes) - limit} more" if len(changes) > limit else ""
    return (
        f"\n[WARNING: this command modified the installed dependencies in "
        f"node_modules ({len(changes)} file(s): {shown}{more}). node_modules is a "
        "shared install: never modify it. Do not try to repair it; make your change "
        "in the repository's own files.]"
    )
