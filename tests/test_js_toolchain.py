"""JavaScript/TypeScript mode of the Quick loop: detection, checks and guard.

Most tests use small fake `vitest`/`tsc` executables (Python scripts writing
the same JSON report shape), so they need no Node. Tests marked `zustand`
run the real toolchain and skip when the prepared install is absent.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from devstill.runtime import workspace as workspace_module
from devstill.runtime.agent_loop import (
    EXAMPLE,
    JS_CHECK_COMMAND,
    SYSTEM,
    TOOLS,
    agent_loop,
    bash_check_kind,
    compare_tests,
    is_type_check,
    js_verdict_text,
    loop_prompt,
)
from devstill.runtime.jstest import (
    Jest,
    JsGuard,
    Vitest,
    clean_message,
    describe_test_run,
    excess_type_errors,
    failing_from_output,
    failure_details,
    parse_report,
    parse_tsc,
    run_test_module,
    tsc_key,
)
from devstill.runtime.loop_context import build_sections, is_test_path, js_outline
from devstill.runtime.toolchain import (
    Toolchain,
    detect,
    glob_regex,
    import_sample,
    link_dependencies,
    parse_vitest_config,
    scratch_location,
)
from devstill.runtime.tools import ToolResult, Workspace

FAR = 1e18
ZUSTAND = Path("/tmp/devstill-retrieval-bench/repos/zustand-5.0.8")

# --- fakes ------------------------------------------------------------------------

FAKE_VITEST = r"""#!{python}
# A tiny stand-in for vitest: tests are lines like
#   it('name', () => expect(read('src/m.js')).toContain('text'))
# and pass when the file contains (or, with not.toContain, lacks) the text.
#   it('name', () => guard(read('src/m.js'), 'text'))
# runs no expect: it throws when the file lacks the text, else passes (fails
# when every test must assert). A source containing HANG never finishes, one
# containing NOREPORT exits without a report.
import json, os, re, sys, time
from pathlib import Path

args = sys.argv[1:]
report = next(a.split("=", 1)[1] for a in args if a.startswith("--outputFile"))
mode = args[0]
named = [a for a in args[1:] if not a.startswith("-")]
everything = sorted(str(p) for p in Path(".").rglob("*.test.js") if "node_modules" not in p.parts)
files = everything if mode == "related" or not named else named
with open(os.environ.get("FAKE_LOG", os.devnull), "a") as log:
    log.write(json.dumps({"cwd": os.getcwd(), "files": files, "args": args}) + "\n")
sources = "".join(p.read_text() for p in Path("src").rglob("*.js"))
if "HANG" in sources:
    time.sleep(60)
if "NOREPORT" in sources:
    sys.exit(1)
TEST = re.compile(
    r"it\('([^']+)',.*?(?:expect\(read\('([^']+)'\)\)\.(toContain|not\.toContain)\("
    r"|guard\(read\('([^']+)'\),\s*)'([^']*)'\)"
)
modules, failed = [], 0
for name in files:
    path = Path(name).resolve()
    text = Path(name).read_text()
    required = "--expect.requireAssertions" in args or "hasAssertions()" in text
    if "SYNTAX ERROR" in text:
        modules.append({"name": str(path), "status": "failed",
                        "message": f"Transform failed: {path}:1:1: Unexpected token",
                        "assertionResults": []})
        failed += 1
        continue
    results = []
    for match in TEST.finditer(text):
        title, target, how, guarded, needle = match.groups()
        line = text.count("\n", 0, match.start()) + 1
        if guarded:
            content = Path(guarded).read_text() if Path(guarded).exists() else ""
            message = (
                "TypeError: guard is not a function" if needle not in content
                else "Error: expected any number of assertion, but got none" if required
                else None
            )
        else:
            content = Path(target).read_text() if Path(target).exists() else ""
            ok = (needle in content) == (how == "toContain")
            message = None if ok else f"AssertionError: expected file to contain '{needle}'"
        results.append({
            "ancestorTitles": ["suite"] if "describe(" in text else [],
            "title": title,
            "fullName": title,
            "status": "failed" if message else "passed",
            "failureMessages": [
                f"{message}\n    at {path}:{line}:20\n    at runTest (node_modules/x.js:1:1)"
            ] if message else [],
        })
        failed += message is not None
    modules.append({"name": str(path), "status": "failed" if any(
        r["status"] == "failed" for r in results) else "passed",
        "message": "", "assertionResults": results})
Path(report).write_text(json.dumps({"numTotalTests": sum(len(m["assertionResults"]) for m in modules),
                                    "testResults": modules}))
print("stdout | fake runner done\nconsole line\n" if "--reporter=default" in args else "")
sys.exit(1 if failed else 0)
"""

FAKE_TSC = r"""#!{python}
# A stand-in for tsc --noEmit: every TYPE_ERROR(x) marker in src/ is an error.
import os, re, sys, json
from pathlib import Path

with open(os.environ.get("FAKE_LOG", os.devnull), "a") as log:
    log.write(json.dumps({"cwd": os.getcwd(), "tsc": True}) + "\n")
errors = []
for path in sorted(Path("src").rglob("*.js")):
    for number, line in enumerate(path.read_text().split("\n"), 1):
        match = re.search(r"TYPE_ERROR\((\w+)\)", line)
        if match:
            errors.append(f"{path.as_posix()}({number},1): error TS2322: Type '{match.group(1)}' is wrong.")
print("\n".join(errors))
sys.exit(2 if errors else 0)
"""


def executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.replace("{python}", sys.executable))
    path.chmod(0o755)


@pytest.fixture
def jsrepo(tmp_path):
    """A small JS project with fake vitest/tsc installs (outside the repo)."""
    return make_jsrepo(tmp_path)


def make_jsrepo(tmp_path):
    install = tmp_path / "install" / "node_modules"
    executable(install / ".bin" / "vitest", FAKE_VITEST)
    executable(install / ".bin" / "tsc", FAKE_TSC)
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "package.json").write_text(
        json.dumps({"name": "calc", "devDependencies": {"vitest": "3"}})
    )
    (repo / "tsconfig.json").write_text("{}")
    (repo / "src" / "m.js").write_text(
        "export function add(a, b) {\n  return a - b\n}\n// TYPE_ERROR(old)\n"
    )
    (repo / "tests" / "m.test.js").write_text(
        "it('keeps export', () => expect(read('src/m.js')).toContain('export function add'))\n"
        "it('known bad', () => expect(read('src/m.js')).toContain('NEVER'))\n"
    )
    (repo / "node_modules").symlink_to(install, target_is_directory=True)
    return repo


def toolchain_for(**overrides):
    values = {
        "language": "js",
        "runner": "vitest",
        "runner_path": "node_modules/.bin/vitest",
        "typescript": True,
        "tsc_path": "node_modules/.bin/tsc",
        "test_dir": "tests",
        "test_suffix": ".test.js",
        "globals": True,
        "dependencies": ["node_modules"],
    }
    values.update(overrides)
    return Toolchain(**values)


def copy_sources(repo, target):
    shutil.copytree(repo, target, ignore=shutil.ignore_patterns("node_modules"))
    link_dependencies(repo, target)
    return target


def js_tools(root, log=None):
    env = {"PATH": os.environ["PATH"]}
    if log is not None:
        env["FAKE_LOG"] = str(log)
    return Workspace(root, env=env, tracked=["src/m.js"])


def originals_from(repo):
    def original(name):
        path = repo / name
        return path.read_bytes() if path.is_file() else None

    return original


def call(name, index=0, **arguments):
    return {
        "id": f"c{index}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def reply(*calls, content=""):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = list(calls)
    return {"message": message, "finish_reason": "tool_calls" if calls else "stop"}


def scripted(*replies):
    queue, seen = list(replies), []

    def chat(messages, tools, deadline):
        seen.append(json.loads(json.dumps({"messages": messages, "tools": tools})))
        return queue.pop(0)

    chat.seen = seen
    return chat


def tool_results(chat, index):
    return [
        m["content"]
        for m in chat.seen[index]["messages"]
        if m["role"] == "tool" and not m["tool_call_id"].startswith("example")
    ]


# --- Python mode stays byte-for-byte the same ------------------------------------


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def test_python_mode_prompt_tools_and_example_are_pinned(tmp_path):
    assert digest(SYSTEM) == (
        "d72a86abd7b4883b8e9ceca370bbbe94d6475de1705c80a3db39be10f78ac2e1"
    )
    assert digest(TOOLS) == (
        "72de2201501dbc8c38c97a3dd2bd23e6bca58eb9a3442fb07a5a46a4283c40dd"
    )
    assert digest(EXAMPLE) == (
        "72b8361b238f15177414ee42e52199130acd8e104fe0ee07e02c17f2a2b90989"
    )
    assert SYSTEM.startswith("You are Devstill Quick, a fast, careful coding agent")
    assert [t["function"]["name"] for t in TOOLS] == [
        "read",
        "python",
        "bash",
        "edit",
        "write",
        "done",
    ]
    assert loop_prompt(None) == (SYSTEM, TOOLS, EXAMPLE)
    assert loop_prompt(Toolchain()) == (SYSTEM, TOOLS, EXAMPLE)
    (tmp_path / "m.py").write_text("x = 1\n")
    for toolchain in (None, Toolchain()):
        chat = scripted(reply(content="nothing to do"), reply(content="still nothing"))
        agent_loop(
            Workspace(tmp_path), "Fix", [], chat, tmp_path, FAR, toolchain=toolchain
        )
        first = chat.seen[0]
        assert first["tools"] == TOOLS
        assert first["messages"][0] == {"role": "system", "content": SYSTEM}
        assert first["messages"][1 : 1 + len(EXAMPLE)] == EXAMPLE


def test_js_mode_prompt_has_the_test_tool_and_project_imports():
    toolchain = toolchain_for(
        test_suffix=".test.tsx",
        testing_library=True,
        import_sample=["import { create } from 'calc'"],
    )
    system, tools, example = loop_prompt(toolchain)
    names = [t["function"]["name"] for t in tools]
    assert names == ["read", "test", "bash", "edit", "write", "done"]
    assert "import { create } from 'calc'" in system
    assert "tests/__devstill_check_<n>.test.tsx" in system
    assert "@testing-library/react" in system and "tsc --noEmit" in system
    bash = next(t for t in tools if t["function"]["name"] == "bash")
    assert (
        "never install, reinstall or modify packages" in bash["function"]["description"]
    )
    calls = [c["function"]["name"] for c in example[1]["tool_calls"]]
    assert calls == ["edit", "test", "done"]
    code = json.loads(example[1]["tool_calls"][1]["function"]["arguments"])["code"]
    assert "from '../src/ratio'" in code and "expect(" in code
    assert "number | null" in example[1]["tool_calls"][0]["function"]["arguments"]
    plain = loop_prompt(toolchain_for(typescript=False, test_suffix=".test.js"))[2]
    assert "src/ratio.js" in plain[0]["content"] and "number" not in plain[0]["content"]
    # No runner: no test tool, the check is a bash command.
    system, tools, example = loop_prompt(toolchain_for(runner=None, runner_path=None))
    assert "test" not in [t["function"]["name"] for t in tools]
    assert "Check your change with bash" in system
    assert example[1]["tool_calls"][1]["function"]["name"] == "bash"


# --- detection --------------------------------------------------------------------


def fake_node(tmp_path):
    executable(tmp_path / "bin" / "node", "#!/bin/sh\nexit 0\n")
    return str(tmp_path / "bin")


def test_detection_prefers_python_unless_the_request_is_about_js(jsrepo, tmp_path):
    path = fake_node(tmp_path)
    js = [{"path": "src/m.js"}]
    assert detect(jsrepo, js, path).language == "js"
    assert detect(jsrepo, [{"path": "app/x.py"}], path).language == "python"
    (jsrepo / "pyproject.toml").write_text("")
    assert detect(jsrepo, [], path).language == "python"  # tie: Python markers win
    assert detect(jsrepo, js, path).language == "js"
    (jsrepo / "package.json").unlink()
    assert detect(jsrepo, js, path).language == "python"  # no package.json


def test_python_projects_with_a_package_json_stay_python(jsrepo, tmp_path):
    path = fake_node(tmp_path)
    (jsrepo / "pyproject.toml").write_text("")
    (jsrepo / "tests" / "test_core.py").write_text("def test_x(): pass\n")
    mixed = [{"path": "pkg/core.py"}, {"path": "pkg/a.js"}, {"path": "pkg/b.js"}]
    assert detect(jsrepo, mixed, path).language == "python"  # top-ranked is Python
    assert detect(jsrepo, mixed[1:] + mixed[:1], path).language == "python"  # 2:1
    clear = [{"path": "src/a.js"}, {"path": "src/b.js"}, {"path": "src/c.ts"}]
    assert detect(jsrepo, clear + [{"path": "pkg/core.py"}], path).language == "js"
    # Without an installed runner JS mode would lose the Python tools: stay.
    (jsrepo / "node_modules").unlink()
    assert detect(jsrepo, clear, path).language == "python"
    # requirements.txt and manage.py mark Python projects too (e.g. Django).
    (jsrepo / "pyproject.toml").unlink()
    (jsrepo / "manage.py").write_text("")
    assert detect(jsrepo, [{"path": "a.py"}, {"path": "b.js"}], path).language == (
        "python"
    )
    (jsrepo / "manage.py").unlink()
    (jsrepo / "requirements-dev.txt").write_text("")
    assert detect(jsrepo, [{"path": "b.js"}], path).language == "python"
    (jsrepo / "requirements-dev.txt").unlink()
    # No Python markers: the majority decides, a tie goes to the top file.
    assert detect(jsrepo, [{"path": "a.py"}, {"path": "b.js"}], path).language == (
        "python"
    )
    assert detect(jsrepo, [{"path": "b.js"}, {"path": "a.py"}], path).language == "js"


def test_detection_reads_package_scripts_and_runner_version(jsrepo, tmp_path):
    path = fake_node(tmp_path)
    (jsrepo / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "test": 'pnpm run "/^test:.*/"',
                    "test:types": "tsc --noEmit",
                    "test:lint": "eslint .",
                    "test:spec": "vitest run",
                    "check": "tsc --noEmit && eslint .",
                    "unit": "CI=1 jest --ci",
                }
            }
        )
    )
    package = jsrepo / "node_modules" / "vitest" / "package.json"
    package.parent.mkdir(parents=True)
    package.write_text(json.dumps({"version": "3.2.4"}))
    toolchain = detect(jsrepo, [{"path": "src/m.js"}], path)
    assert toolchain.test_scripts == ["test:spec", "unit"]
    assert toolchain.type_scripts == ["test:types"]
    assert toolchain.runner_version == "3.2.4"
    shutil.rmtree(package.parent)


def test_detection_finds_runner_typecheck_test_dir_and_imports(jsrepo, tmp_path):
    path = fake_node(tmp_path)
    (jsrepo / "package.json").write_text(
        json.dumps(
            {
                "name": "calc",
                "dependencies": {"react": "19"},
                "devDependencies": {"@testing-library/react": "16"},
            }
        )
    )
    (jsrepo / "vitest.config.mts").write_text(
        "export default defineConfig({\n  test: {\n    // dir: 'nope',\n"
        "    globals: true,\n    dir: 'tests',\n"
        "    coverage: { include: ['src/**/'] },\n  },\n})\n"
    )
    (jsrepo / "tests" / "a.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\nimport './setup'\n"
        "import {\n  add,\n  sub,\n} from 'calc'\nimport { helper } from './test-utils'\n"
    )
    toolchain = detect(jsrepo, [{"path": "src/m.ts"}], path)
    assert (toolchain.runner, toolchain.runner_path) == (
        "vitest",
        "node_modules/.bin/vitest",
    )
    assert toolchain.typescript and toolchain.tsc_path == "node_modules/.bin/tsc"
    assert (toolchain.test_dir, toolchain.test_suffix) == ("tests", ".test.tsx")
    assert toolchain.globals and toolchain.react and toolchain.testing_library
    assert toolchain.import_sample == [
        "import { describe, expect, it } from 'vitest'",
        "import { add, sub } from 'calc'",
    ]
    assert toolchain.scratch_name(2) == "tests/__devstill_check_2.test.tsx"
    # Without node on PATH nothing can run.
    blind = detect(jsrepo, [{"path": "src/m.ts"}], str(tmp_path / "empty"))
    assert blind.js and blind.runner is None and not blind.typescript


def test_jest_is_used_when_the_test_script_names_it(jsrepo, tmp_path):
    path = fake_node(tmp_path)
    executable(jsrepo / "node_modules" / ".bin" / "jest", "#!/bin/sh\n")
    (jsrepo / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    toolchain = detect(jsrepo, [{"path": "src/m.js"}], path)
    assert toolchain.runner == "jest" and toolchain.globals


def test_vitest_config_parsing_and_scratch_location(tmp_path):
    config = parse_vitest_config(
        "/* test: { dir: 'x' } */ export default { test: { include: "
        "['src/**/*.spec.ts'], coverage: { include: ['lib/**'] } } }"
    )
    assert config == {"include": ["src/**/*.spec.ts"]}
    assert parse_vitest_config("export default { plugins: [] }") == {}
    pattern = glob_regex("**/*.{test,spec}.?(c|m)[jt]s?(x)")
    assert pattern.match("a/b/x.test.tsx") and pattern.match("x.spec.mjs")
    assert not pattern.match("x.tests.ts")
    (tmp_path / "src").mkdir()
    assert scratch_location(tmp_path, [], config, typescript=True, react=False) == (
        "src",
        ".spec.ts",
    )
    assert scratch_location(tmp_path, [], {}, typescript=False, react=False) == (
        ".",
        ".test.js",
    )


def test_import_sample_ranks_value_imports_and_skips_helpers(tmp_path):
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "a.test.ts").write_text(
        "import type { Api } from 'lib'\nimport { make } from 'lib'\n"
        "import { render } from '@testing-library/react'\nimport x from '../src/x'\n"
    )
    (tmp_path / "t" / "b.test.ts").write_text(
        "import { make, other } from 'lib'\nimport type { Api } from 'lib/types'\n"
    )
    assert import_sample(tmp_path, ["t/a.test.ts", "t/b.test.ts"], "lib") == [
        "import { make } from 'lib'",  # one line per module, value imports first
        "import x from '../src/x'",
        "import type { Api } from 'lib/types'",
    ]


# --- bash commands ----------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "npx vitest run tests/a.test.ts",
        "pnpm vitest run",
        "pnpm exec vitest related src/a.ts --run",
        "node_modules/.bin/vitest run tests/x.test.tsx 2>&1 | tail -20",
        "cd app && npx jest src/a.test.js",
        "npm test",
        "pnpm test -- -t name",
        "yarn test",
        "bun test",
        "npm run test",
        "node --test test/",
        "npx mocha",
        "CI=1 vitest run",
        "npx --yes vitest run",
        "npm exec vitest run",
        "pnpm test:spec",
        "pnpm run test:spec -- tests/a.test.ts",
        "jest --version; npx vitest run",
    ],
)
def test_js_test_runs_are_recognized(command):
    assert bash_check_kind(command, True, ("test:spec",)) == "tests"
    # Python mode is unchanged: only `npm test` counted there before.
    python = "tests" if command in ("npm test", "npm run test") else None
    assert bash_check_kind(command) == python


@pytest.mark.parametrize(
    "command",
    [
        "npx vitest list",
        "npx vitest --version",
        "npx jest --listTests",
        "vitest --help",
        "grep -rn vitest src",
        "cat vitest.config.mts",
        "node -e \"console.log(require('./x'))\"",
        'node -e "console.assert(x === 1)"',
        "npx tsc --noEmit",
        "npx vitest -v",
        "vitest-report.json",
        # Package scripts that type-check, lint or format do not run tests.
        "npm run test:types",
        "pnpm test:lint",
        "pnpm test:format",
    ],
)
def test_js_commands_that_do_not_verify(command):
    assert bash_check_kind(command, True, ("test:spec",)) is None


def test_node_scripts_that_assert_and_failing_js_commands():
    def js(command):
        return bash_check_kind(command, True)

    assert js("node -e \"require('node:assert').equal(f(), 2)\"") == "script"
    assert js("node --input-type=module -e 'if (x) throw new Error()'") == "script"
    assert js("node <<'EOF'\nassert(f() === 1)\nEOF") == "script"
    assert bash_check_kind("node -e \"require('node:assert').equal(f(), 2)\"") is None
    # Python behavior is unchanged, also next to JS commands.
    for kind in (bash_check_kind, js):
        assert kind("python -m pytest -q tests") == "tests"
        assert kind("pytest --version") == "tests"
        assert kind("pytest tests/jest -v") == "tests"
        assert kind("python -c 'assert 1'") == "script"
        assert kind("pytest -q; jest --version") == "tests"
        assert kind('pytest -k "(jest) -v"') == "tests"
        assert kind("python -m pytest 'tests/test_x.py::test_a[(jest) -v]'") == "tests"
        assert (
            kind("python -c 'import json; assert json.loads(\"1\") == 1'; vitest -v")
            == "script"
        )
    assert bash_check_kind("npm run test:types") == "tests"  # as at HEAD
    for evidence in (
        "npx tsc --noEmit",
        "node x.js",
        "pnpm run typecheck",
        "jest",
        "pnpm test:types",
    ):
        assert JS_CHECK_COMMAND.search(evidence), evidence
    for other in ("grep -rn node src", "ls node_modules", "echo tsc", "node-gyp x"):
        assert not JS_CHECK_COMMAND.search(other), other


def test_js_command_patterns_do_not_backtrack_exponentially():
    started = time.perf_counter()
    for command in (
        "node " + "--ab " * 40 + "x.js",
        "npx " + "--ab " * 40 + "x",
        "node " + "--ab=1 " * 40 + "-x",
    ):
        bash_check_kind(command, True)
        JS_CHECK_COMMAND.search(command)
        is_type_check(command)
    assert time.perf_counter() - started < 1.0


def test_type_check_commands():
    for command in (
        "npx tsc --noEmit",
        "node_modules/.bin/tsc --noEmit -p . 2>&1",
        "cd app && npx tsc --noEmit",
        "pnpm typecheck",
        "pnpm test:types",
    ):
        assert is_type_check(command, ("test:types",)), command
    for command in ("npx tsc --noEmit && npx vitest run", "pnpm test", "echo tsc"):
        assert not is_type_check(command, ("test:types",)), command


# --- report parsing ------------------------------------------------------------------


def test_parse_report_ids_statuses_duplicates_and_load_failures(tmp_path):
    data = {
        "testResults": [
            {
                "name": str(tmp_path / "tests" / "a.test.ts"),
                "status": "failed",
                "message": "",
                "assertionResults": [
                    {"ancestorTitles": ["store"], "title": "sets", "status": "passed"},
                    {
                        "ancestorTitles": ["store"],
                        "title": "sets",
                        "status": "failed",
                        "failureMessages": ["AssertionError: x\n    at f (a.ts:1:1)"],
                    },
                    {"ancestorTitles": [], "title": "later", "status": "todo"},
                ],
            },
            {
                "name": str(tmp_path / "tests" / "b.test.ts"),
                "status": "failed",
                "message": "Failed to resolve import 'nope'",
                "assertionResults": [],
            },
        ]
    }
    parsed = parse_report(data, tmp_path)
    assert parsed["tests"] == {
        "tests/a.test.ts > store > sets": "passed",
        "tests/a.test.ts > store > sets [2]": "failed",
        "tests/a.test.ts > later": "skipped",
        "tests/b.test.ts > (module failed to load)": "failed",
    }
    assert parsed["load_errors"] == {
        "tests/b.test.ts": "Failed to resolve import 'nope'"
    }
    assert (
        "AssertionError: x"
        in (parsed["failures"]["tests/a.test.ts > store > sets [2]"]["message"])
    )


VITEST_TEXT = """
 RUN  v3.2.4 /w

stdout | tests/c.test.tsx > x
hello { a: 1 }

 ❯ |proj| tests/c.test.tsx (2 tests | 1 failed) 21ms
   × equal 14ms

⎯⎯⎯⎯⎯⎯⎯ Failed Tests 1 ⎯⎯⎯⎯⎯⎯⎯

 FAIL  |proj| tests/c.test.tsx > group > equal
AssertionError: expected { a: 1 } to deeply equal { a: 2 }

- Expected
+ Received

  {
-   "a": 2,
+   "a": 1,
  }

 ❯ tests/c.test.tsx:6:24
      6|   expect(x).toEqual({ a: 2 })

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[1/1]⎯

 FAIL  tests/d.test.tsx [ tests/d.test.tsx ]
TypeError: s.boom is not a function
 ❯ tests/d.test.tsx:3:13
"""


def test_failure_details_failing_ids_and_tsc_output():
    details = failure_details(VITEST_TEXT)
    message, location = details["tests/c.test.tsx > group > equal"]
    assert message.startswith("AssertionError") and '+   "a": 1,' in message
    assert location == ("tests/c.test.tsx", 6)
    assert details["tests/d.test.tsx"] == (
        "TypeError: s.boom is not a function",
        ("tests/d.test.tsx", 3),
    )
    assert failing_from_output(VITEST_TEXT) == ["tests/c.test.tsx > group > equal"]
    jest = "FAIL src/a.test.js\n  ● math › adds\n\n    expect(received)\n"
    assert failing_from_output(jest) == ["src/a.test.js > math > adds"]
    errors = parse_tsc(
        "src/a.ts(3,5): error TS2322: Type 'string' is not assignable to type 'number'.\n"
        "src/b.ts(10,1): error TS2345: Argument of type 'X'\n"
        "  is not assignable to parameter of type 'Y'.\n"
        "error TS5023: Unknown compiler option 'x'.\n"
    )
    assert [(e["file"], e["line"], e["code"]) for e in errors] == [
        ("src/a.ts", 3, "TS2322"),
        ("src/b.ts", 10, "TS2345"),
        ("", 0, "TS5023"),
    ]
    assert "\nis not assignable" in errors[1]["message"]
    moved = dict(errors[0], line=30, col=1)
    assert tsc_key(moved) == tsc_key(errors[0])


def test_tsc_files_with_parentheses_and_counted_type_errors():
    errors = parse_tsc(
        "app/(marketing)/page.tsx(3,7): error TS2322: Type 'string' is not "
        "assignable to type 'number'.\nsrc/ok.ts(2,1): error TS2304: Cannot find name 'x'.\n"
    )
    assert [(e["file"], e["line"]) for e in errors] == [
        ("app/(marketing)/page.tsx", 3),
        ("src/ok.ts", 2),
    ]

    def error(line, message="Object is possibly 'undefined'."):
        return {
            "file": "src/a.ts",
            "line": line,
            "col": 1,
            "code": "TS2532",
            "message": message,
        }

    before = [error(10)]
    # The old error moved (10 -> 14) and an identical one appeared at 5.
    assert excess_type_errors(before, [error(5), error(14)]) == [error(5)]
    assert excess_type_errors(before, [error(14)]) == []
    # Of identical errors, one at a line the old one did not have is new.
    assert excess_type_errors(before, [error(10), error(20)]) == [error(20)]
    assert excess_type_errors([], [error(1, "x")]) == [error(1, "x")]


def test_messages_lose_vitest_frames_and_code_frames():
    text = (
        "TestingLibraryElementError: Unable to find an element with the text: nope\n"
        " ❯ Object.getElementError ../node_modules/.pnpm/@testing-library+dom/dist/"
        "config.js:37:19\n"
        " ❯ ../node_modules/.pnpm/@vitest+runner/dist/chunk-hooks.js:752:20\n"
        "      6|   expect(x).toEqual({ a: 2 })\n"
        "       |                        ^\n"
        "    at f (/w/a.ts:1:1)\n"
        "- Expected\n+ Received\n"
    )
    assert clean_message(text) == (
        "TestingLibraryElementError: Unable to find an element with the text: nope\n"
        "- Expected\n+ Received"
    )


def test_describe_test_run_shows_failures_lines_and_records(tmp_path):
    name = "tests/__devstill_check_1.test.tsx"
    code = "import { f } from '../src/f'\n\nit('works', () => {\n  expect(f()).toBe(2)\n})\n"
    data = {
        "testResults": [
            {
                "name": str(tmp_path / name),
                "status": "failed",
                "assertionResults": [
                    {
                        "ancestorTitles": [],
                        "title": "works",
                        "status": "failed",
                        "failureMessages": [
                            (
                                "AssertionError: expected 1 to be 2 // Object.is "
                                f"equality\n    at {tmp_path / name}:4:15"
                            )
                        ],
                    },
                    {"ancestorTitles": [], "title": "keeps", "status": "passed"},
                ],
            }
        ]
    }
    result = describe_test_run(
        ToolResult("[exit code 1]", ok=False), data, tmp_path, name, code
    )
    assert not result.ok
    assert "FAIL works\nAssertionError: expected 1 to be 2" in result.text
    assert "at check line 4: expect(f()).toBe(2)" in result.text
    assert "[1 failed, 1 passed]" in result.text
    record = result.meta["record"]
    assert record["tests"] == {"works": "failed", "keeps": "passed"}
    assert record["failures"][0]["line"] == 4 and record["passed"] == 1
    json.dumps(record)  # JSON-native
    data["testResults"][0]["assertionResults"].pop(0)
    ok = describe_test_run(ToolResult("[exit code 0]"), data, tmp_path, name, code)
    assert ok.ok and ok.text == "1 passed"
    empty = describe_test_run(
        ToolResult("[exit code 1]", ok=False), {"testResults": []}, tmp_path, name, code
    )
    assert not empty.ok and "No test ran" in empty.text


def test_compare_tests_names_broken_same_changed_and_fixed():
    def run(tests, failures):
        return {
            "tests": tests,
            "failures": [{"name": n, "message": m} for n, m in failures.items()],
        }

    now = run(
        {"a": "failed", "b": "failed", "c": "failed", "d": "passed"},
        {"a": "x", "b": "same", "c": "new"},
    )
    before = run(
        {"a": "passed", "b": "failed", "c": "failed", "d": "failed"},
        {"b": "same", "c": "old", "d": "boom"},
    )
    verdict = compare_tests(now, before)
    assert verdict["broke"] == ["a"] and verdict["same"] == ["b"]
    assert verdict["changed"] == ["c"] and verdict["fixed"] == ["d"]
    text = js_verdict_text(verdict)
    assert "'a' passed on the original code and fail now" in text
    assert "'d' failed on the original code and pass now" in text
    # The run on the original code uses another scratch module number.
    load = 'Failed to resolve import "../../src/x" from "tests/__devstill_check_{}.test.ts"'
    same = compare_tests(
        {"tests": {}, "load_error": load.format(1)},
        {"tests": {}, "load_error": load.format(2)},
    )
    assert same["load_same"]
    assert "fails to load the same way on the original code" in js_verdict_text(same)
    named = compare_tests(
        run({"a": "failed"}, {"a": "x at tests/__devstill_check_3.test.ts:2"}),
        run({"a": "failed"}, {"a": "x at tests/__devstill_check_4.test.ts:2"}),
    )
    assert named["same"] == ["a"] and named["changed"] == []


def test_runner_commands_disable_coverage_and_require_assertions():
    vitest = Vitest("node_modules/.bin/vitest", "3.2.4")
    argv = vitest.files(["t.test.ts"], "/r.json", require_assertions=True)
    assert "--coverage.enabled=false" in argv and "--expect.requireAssertions" in argv
    assert vitest.footer() is None
    assert "--coverage.enabled=false" in vitest.suite("/r.json")
    assert "--coverage.enabled=false" in vitest.related(["src/a.ts"], "/r.json")
    assert "--expect.requireAssertions" not in vitest.files(["t.test.ts"], "/r.json")
    old = Vitest("node_modules/.bin/vitest", "1.6.0")
    assert "--expect.requireAssertions" not in old.files(
        ["t.test.ts"], "/r.json", require_assertions=True
    )
    assert "hasAssertions()" in old.footer() and old.footer().startswith("\n")
    jest = Jest("node_modules/.bin/jest")
    assert "--coverage=false" in jest.files(["t.test.js"], "/r.json")
    assert "hasAssertions()" in jest.footer()


# --- workspace: dependencies are linked, never content ---------------------------------


def test_dependencies_are_linked_and_never_treated_as_content(jsrepo, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    assert link_dependencies(jsrepo, work) == ["node_modules"]
    link = work / "node_modules"
    assert link.is_symlink()
    assert link.resolve() == (jsrepo / "node_modules").resolve()  # resolved target
    assert link_dependencies(jsrepo, work) == []  # idempotent
    (work / "src").mkdir()
    (work / "src" / "m.js").write_text("x\n")
    tools = Workspace(work, resolve=lambda n: workspace_module.source_path(work, n))
    assert tools._files() == {"src/m.js"}
    with pytest.raises(ValueError):
        tools.write("node_modules/.bin/evil", "x")
    with pytest.raises(ValueError):
        workspace_module.guard(work, None, "node_modules/pkg/index.js")
    files, _ = workspace_module.changes(
        jsrepo, work, {}, ["src/m.js", "node_modules/.bin/vitest"], strict=False
    )
    assert list(files) == ["src/m.js"]
    created = tools.bash("touch node_modules/.bin/new && touch scratch.txt", 10)
    assert "scratch.txt" in created.text and "node_modules" not in created.text
    (tmp_path / "install" / "node_modules" / ".bin" / "new").unlink()


def test_snapshots_skip_dependency_directories(tmp_path):
    from devstill.config import DevstillConfig

    repo = tmp_path / "repo"
    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "node_modules" / "pkg" / "index.js").write_text("module.exports = 1\n")
    (repo / "src").mkdir()
    (repo / "src" / "a.js").write_text("export const a = 1\n")
    config = DevstillConfig()
    config.files.exclude = []  # even when nothing excludes it
    work = tmp_path / "work"
    work.mkdir()
    manifest = workspace_module.snapshot(repo, work, config, True)
    assert "src/a.js" in manifest
    assert not any("node_modules" in name for name in manifest)
    assert not (work / "node_modules").exists()


def test_js_context_outlines_and_finds_js_tests(tmp_path):
    assert is_test_path("src/__tests__/a.ts") and is_test_path("src/a.spec.tsx")
    assert not is_test_path("src/spec.ts")
    assert js_outline("export function a() {}\n  const inner = 1\nclass B {}\n") == (
        "L1 export function a\nL3 class B"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.ts").write_text(
        "export function subtract(a: number, b: number) {\n  return a - b\n}\n"
        "export const VERSION = 1\n"
    )
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)  # a real directory
    (tmp_path / "node_modules" / "pkg" / "x.test.js").write_text("subtract(1)\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "m.test.ts").write_text(
        "import { subtract } from '../src/m'\nexpect(subtract(3, 2)).toBe(1)\n"
    )
    records = [
        {"path": "src/m.ts", "lines": [1, 3], "symbol": "subtract", "kind": "function"}
    ]
    sections = build_sections(tmp_path, records, language="js")
    assert sections[0].startswith(
        "### src/m.ts — 4 lines\nOutline:\nL1 export function"
    )
    assert "```typescript" in sections[0]
    assert sections[-1] == (
        "Related tests: tests/m.test.ts (2 mentions)\nExisting usage in tests:\n"
        "tests/m.test.ts:2: expect(subtract(3, 2)).toBe(1)"
    )
    assert "node_modules" not in "\n".join(sections)
    # Python output does not change for the same records.
    python = build_sections(tmp_path, records)
    assert not any(s.startswith("Related tests") for s in python)
    assert "Outline" not in python[0]


# --- the test tool ------------------------------------------------------------------


def test_scratch_module_is_run_alone_then_always_removed(jsrepo, tmp_path):
    work = copy_sources(jsrepo, tmp_path / "work")
    log = tmp_path / "runs.jsonl"
    tools = js_tools(work, log)
    toolchain = toolchain_for()
    passing = "it('subtracts', () => expect(read('src/m.js')).toContain('a - b'))\n"
    result = run_test_module(tools, toolchain, passing, 30)
    assert result.ok and result.text.startswith("stdout | fake runner done")
    assert result.text.endswith("1 passed")
    assert result.meta["record"]["tests"] == {"subtracts": "passed"}
    runs = [json.loads(line) for line in log.read_text().splitlines()]
    assert runs[-1]["files"] == ["tests/__devstill_check_1.test.js"]  # only it
    assert sorted(p.name for p in (work / "tests").iterdir()) == ["m.test.js"]
    assert tools.changed == [] and "NOT part of the change" not in result.text
    failing = "it('adds', () => expect(read('src/m.js')).toContain('a + b'))\n"
    result = run_test_module(tools, toolchain, failing, 30)
    assert not result.ok and "FAIL adds" in result.text
    assert "at check line 1" in result.text
    broken = run_test_module(tools, toolchain, "SYNTAX ERROR expect(", 30)
    assert not broken.ok and "The test module failed to load" in broken.text
    assert "relative imports start from tests/" in broken.text
    assert broken.meta["record"]["load_error"]
    assert sorted(p.name for p in (work / "tests").iterdir()) == ["m.test.js"]
    # A missing test directory is created for the run and removed again.
    nested = toolchain_for(test_dir="spec/unit")
    assert run_test_module(tools, nested, passing, 30).ok
    assert not (work / "spec").exists()


# --- the loop in JS mode ------------------------------------------------------------


def js_loop(jsrepo, tmp_path, chat, guard=False, toolchain=None, **kwargs):
    work = copy_sources(jsrepo, tmp_path / "work")
    tools = js_tools(work, tmp_path / "runs.jsonl")
    original = originals_from(jsrepo)
    toolchain = toolchain or toolchain_for()
    js_guard = None
    if guard:
        js_guard = JsGuard(tools, toolchain, original)
        js_guard.start(copy_sources(jsrepo, tmp_path / "pristine"))
    result = agent_loop(
        tools,
        "Make add add",
        [],
        chat,
        tmp_path,
        FAR,
        originals=original,
        toolchain=toolchain,
        guard=js_guard,
        **kwargs,
    )
    if js_guard is not None:
        js_guard.cleanup()
    return result, work


EDIT = call("edit", 0, path="src/m.js", oldText="a - b", newText="a + b")
PROVES = "it('adds', () => expect(read('src/m.js')).toContain('a + b'))\n"


def test_js_check_that_fails_on_the_original_finishes_without_done(jsrepo, tmp_path):
    chat = scripted(reply(EDIT, call("test", 1, code=PROVES), content="Fixed add."))
    result, work = js_loop(jsrepo, tmp_path, chat, guard=True)
    assert result["status"] == "applied_unvalidated", result
    assert result["finished"] == "verified_check" and result["model_calls"] == 1
    assert result["verified"] and result["changed_files"] == ["src/m.js"]
    assert result["commands"][0]["command"] == "test:\n" + PROVES
    assert not any("__devstill_check" in p.name for p in (work / "tests").iterdir())
    assert "a + b" in (work / "src" / "m.js").read_text()  # restored after the swap
    tools = [t["function"]["name"] for t in chat.seen[0]["tools"]]
    assert "test" in tools and "python" not in tools


def test_js_check_passing_on_the_original_is_refused_once(jsrepo, tmp_path):
    weak = "it('exports', () => expect(read('src/m.js')).toContain('export'))\n"
    chat = scripted(
        reply(EDIT, call("test", 1, code=weak), call("done", 2, summary="x")),
        reply(call("test", 0, code=PROVES), call("done", 1, summary="fixed")),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat)
    assert result["status"] == "applied_unvalidated" and result["model_calls"] == 2
    assert "also passes on the original code" in tool_results(chat, 1)[-1]


def test_js_latest_check_is_rerun_after_a_later_edit(jsrepo, tmp_path):
    chat = scripted(
        reply(
            EDIT,
            call("test", 1, code=PROVES),
            call("edit", 2, path="src/m.js", oldText="a + b", newText="a * b"),
            call("done", 3, summary="tidied"),
        ),
        reply(
            call("edit", 0, path="src/m.js", oldText="a * b", newText="a + b"),
            call("done", 1, summary="fixed"),
        ),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat)
    assert result["model_calls"] == 2 and result["verified"]
    rejected = tool_results(chat, 1)[-1]
    assert "fails after your last edit" in rejected and "FAIL adds" in rejected
    assert "failed on the original code" not in rejected  # same failure there
    assert result["commands"][-1]["automatic"]


def test_failing_js_check_is_compared_with_the_original(jsrepo, tmp_path):
    keeps = "it('keeps export', () => expect(read('src/m.js')).toContain('export'))\n"
    chat = scripted(
        reply(
            call(
                "edit",
                0,
                path="src/m.js",
                oldText="export function",
                newText="function",
            ),
            call("test", 1, code=keeps + PROVES),
        ),
        reply(call("done", 0, summary="x")),
        reply(call("done", 0, summary="x")),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat)
    shown = tool_results(chat, 1)[-1]
    assert "The results above are from your current, edited code" in shown
    assert "ran on a pristine copy of the original code" in shown
    assert "'keeps export' passed on the original code and fail now" in shown
    assert "'adds' fail the same way on the original code" in shown
    assert result["status"] == "failed" or not result["verified"]


NO_EXPECT = "it('adds', () => guard(read('src/m.js'), 'a + b'))\n"


@pytest.mark.parametrize("version", [None, "3.2.4"])
def test_js_check_whose_expects_never_run_does_not_finish(jsrepo, tmp_path, version):
    # Passes with no expect run now and throws on the original code: it
    # proves nothing, so the runner fails it and the loop does not finish.
    chat = scripted(
        reply(EDIT, call("test", 1, code=NO_EXPECT)),
        reply(call("test", 0, code=PROVES)),
    )
    result, work = js_loop(
        jsrepo, tmp_path, chat, toolchain=toolchain_for(runner_version=version)
    )
    shown = tool_results(chat, 1)[-1]
    assert "FAIL adds\nNo expect ran in this test" in shown
    assert result["finished"] == "verified_check" and result["model_calls"] == 2
    first = json.loads((tmp_path / "runs.jsonl").read_text().splitlines()[0])
    assert ("--expect.requireAssertions" in first["args"]) == bool(version)
    assert "--coverage.enabled=false" in first["args"]
    assert not any("__devstill_check" in p.name for p in (work / "tests").iterdir())


def test_js_auto_finish_needs_a_real_failure_on_the_original(jsrepo, tmp_path):
    # The runner writes no report on the original code: unknown, not a failure.
    (jsrepo / "src" / "m.js").write_text(
        (jsrepo / "src" / "m.js").read_text() + "// NOREPORT\n"
    )
    edit = call(
        "edit",
        0,
        path="src/m.js",
        edits=[
            {"oldText": "a - b", "newText": "a + b"},
            {"oldText": "// NOREPORT", "newText": "// ok"},
        ],
    )
    chat = scripted(
        reply(edit, call("test", 1, code=PROVES)),
        reply(call("done", 0, summary="fixed")),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat)
    assert result["status"] == "applied_unvalidated"
    assert result["model_calls"] == 2 and result.get("finished") is None


def test_js_check_timing_out_on_the_original_runs_there_once(jsrepo, tmp_path):
    (jsrepo / "src" / "m.js").write_text(
        (jsrepo / "src" / "m.js").read_text() + "// HANG\n"
    )
    fixed = "it('fixed', () => expect(read('src/m.js')).toContain('// fixed'))\n"
    chat = scripted(
        reply(
            call("edit", 0, path="src/m.js", oldText="// HANG", newText="// fixed"),
            call("test", 1, code=fixed),
        ),
        reply(call("done", 0, summary="fixed")),
    )
    started = time.perf_counter()
    result, _ = js_loop(jsrepo, tmp_path, chat, bash_timeout=2.0)
    assert result["status"] == "applied_unvalidated" and result["model_calls"] == 2
    runs = [
        json.loads(line) for line in (tmp_path / "runs.jsonl").read_text().splitlines()
    ]
    scratch = [r for r in runs if any("__devstill_check" in f for f in r["files"])]
    assert len(scratch) == 2  # the check, then once on the original (timed out)
    assert time.perf_counter() - started < 15


def test_scratch_module_that_does_not_parse_is_a_test_bug(jsrepo, tmp_path):
    chat = scripted(
        reply(EDIT, call("test", 1, code="SYNTAX ERROR expect(")),
        reply(call("test", 0, code=PROVES)),
        reply(call("done", 0, summary="fixed")),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat)
    shown = tool_results(chat, 1)[-1]
    assert "The test module failed to load" in shown
    # A module that does not parse is a test bug: no run on the original code,
    # and it does not hold back the next passing check.
    assert "the test module itself does not parse" in shown
    assert "At your test module line 1, column 1:\n> 1 | SYNTAX ERROR expect(" in shown
    assert result["commands"][0]["check_problem"] and not result["commands"][0]["check"]
    assert result["finished"] == "verified_check" and result["model_calls"] == 2
    runs = (tmp_path / "runs.jsonl").read_text().splitlines()
    assert len(runs) == 3  # broken module, passing check, passing check's original


def test_agent_type_check_with_only_old_errors_is_not_held_against_it(jsrepo, tmp_path):
    tsc = call("bash", 2, command="node_modules/.bin/tsc --noEmit")
    chat = scripted(
        reply(EDIT, call("test", 1, code=PROVES), tsc, call("done", 3, summary="x")),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat, guard=True)
    shown = tool_results(chat, 1)[2] if len(chat.seen) > 1 else None
    assert result["status"] == "applied_unvalidated" and result["model_calls"] == 1
    assert shown is None
    tsc_run = next(c for c in result["commands"] if "tsc" in c["command"])
    assert not tsc_run["check"]
    assert (
        "also occurs on the original code: your change did not cause it"
        in (tsc_run["output"])
    )


def test_agent_type_check_with_a_new_error_still_counts(jsrepo, tmp_path):
    edit = call(
        "edit", 0, path="src/m.js", oldText="a - b", newText="a + b // TYPE_ERROR(old)"
    )
    tsc = call("bash", 2, command="node_modules/.bin/tsc --noEmit")
    chat = scripted(
        reply(edit, call("test", 1, code=PROVES), tsc, call("done", 3, summary="x")),
        *[reply(call("done", 0, summary="x")) for _ in range(4)],
    )
    result, _ = js_loop(jsrepo, tmp_path, chat, guard=True, max_turns=5)
    assert (
        "new with your change:\nsrc/m.js(2,1): error TS2322"
        in (tool_results(chat, 1)[2])
    )
    refusals = [tool_results(chat, i)[-1] for i in range(1, len(chat.seen))]
    assert "your latest check failed" in refusals[1]
    # The guard reports the second identical error as new, too.
    assert "The type check (tsc --noEmit) reports new errors" in refusals[2]
    assert result["status"] == "failed"


def test_unknown_tools_in_js_mode_list_the_js_tools(jsrepo, tmp_path):
    chat = scripted(
        reply(EDIT, call("python", 1, code="assert 1")),
        reply(call("test", 0, code=PROVES)),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat)
    assert "Available: read, test, bash, edit, write, done" in tool_results(chat, 1)[-1]
    assert result["finished"] == "verified_check"


def test_editing_a_scratch_module_explains_that_it_is_gone(jsrepo, tmp_path):
    scratch = "tests/__devstill_check_1.test.js"
    chat = scripted(
        reply(EDIT, call("edit", 1, path=scratch, oldText="a", newText="b")),
        reply(call("test", 0, code=PROVES)),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat)
    assert "was a temporary test module" in tool_results(chat, 1)[-1]
    assert result["finished"] == "verified_check"


# --- the JS guard ---------------------------------------------------------------------


def started_guard(jsrepo, tmp_path):
    work = copy_sources(jsrepo, tmp_path / "work")
    log = tmp_path / "runs.jsonl"
    tools = js_tools(work, log)
    guard = JsGuard(tools, toolchain_for(), originals_from(jsrepo))
    guard.start(copy_sources(jsrepo, tmp_path / "pristine"))
    return guard, tools, work, log


def test_guard_reports_only_new_failures_and_new_type_errors(jsrepo, tmp_path):
    guard, tools, work, log = started_guard(jsrepo, tmp_path)
    try:
        tools.edit(
            "src/m.js",
            [
                {"oldText": "export function add", "newText": "// moved\nfunction add"},
                {
                    "oldText": "return a - b",
                    "newText": "return a - b // TYPE_ERROR(new)",
                },
            ],
        )
        guard.changed()
        found = guard.regressions(30)
        assert found["regressions"] == ["tests/m.test.js > keeps export"]
        assert found["added"] == []  # 'known bad' failed on the pristine code too
        assert found["type_errors"] == [
            "src/m.js(3,1): error TS2322: Type 'new' is wrong."
        ]  # TYPE_ERROR(old) moved to line 5: same file, code and message
        assert "FAIL tests/m.test.js > keeps export" in found["output"]
        assert "tsc --noEmit:" in found["output"]
        assert guard.known_failures(["tests/m.test.js > known bad"], 10) == [
            "tests/m.test.js > known bad"
        ]
        assert guard.known_failures(["tests/m.test.js > keeps export"], 10) == []
        tools.edit(
            "src/m.js",
            [
                {"oldText": "// moved\nfunction add", "newText": "export function add"},
                {"oldText": " // TYPE_ERROR(new)", "newText": ""},
            ],
        )
        guard.changed()
        assert guard.regressions(30) is None
        runs = [json.loads(line) for line in log.read_text().splitlines()]
        assert runs and all(
            run["cwd"] != str(work) for run in runs
        )  # never in the workspace
    finally:
        guard.cleanup()


def test_guard_reports_a_new_type_error_identical_to_an_old_one(jsrepo, tmp_path):
    guard, tools, _, _ = started_guard(jsrepo, tmp_path)
    try:
        tools.edit(
            "src/m.js",
            [
                {
                    "oldText": "  return a - b",
                    "newText": "  return a + b // TYPE_ERROR(old)",
                }
            ],
        )
        guard.changed()
        found = guard.regressions(30)
        assert found["type_errors"] == [
            "src/m.js(2,1): error TS2322: Type 'old' is wrong."
        ]
        errors = parse_tsc("src/m.js(4,1): error TS2322: Type 'old' is wrong.\n")
        assert guard.type_errors_beyond_original(errors, tools.root, 10) == []
        assert len(guard.type_errors_beyond_original(errors * 2, tools.root, 10)) == 1
    finally:
        guard.cleanup()


def test_guard_counts_failing_tests_the_agent_added(jsrepo, tmp_path):
    guard, tools, _, _ = started_guard(jsrepo, tmp_path)
    try:
        tools.write(
            "tests/new.test.js",
            "it('new one', () => expect(read('src/m.js')).toContain('a * b'))\n",
        )
        guard.changed()
        found = guard.regressions(30)
        assert found["added"] == ["tests/new.test.js > new one"]
        assert found["regressions"] == []
    finally:
        guard.cleanup()


def test_guard_without_results_in_time_reports_nothing(jsrepo, tmp_path):
    guard, tools, _, _ = started_guard(jsrepo, tmp_path)
    try:
        tools.edit(
            "src/m.js", [{"oldText": "export function add", "newText": "function add"}]
        )
        guard.changed()
        assert guard.regressions(0.0) is None  # unknown is never a regression
    finally:
        guard.cleanup()
    assert not guard.current.exists()


def test_guard_close_stops_runs_and_refuses_new_ones(jsrepo, tmp_path):
    guard, tools, _, _ = started_guard(jsrepo, tmp_path)
    guard.close()
    tools.edit("src/m.js", [{"oldText": "a - b", "newText": "a + b"}])
    guard.changed()
    assert guard._runs == {} and guard.regressions(1) is None
    guard.cleanup()


# --- the real toolchain (zustand) -------------------------------------------------------


def node_path():
    nvm = Path.home() / ".nvm/versions/node/v24.14.1/bin"
    return os.pathsep.join([str(nvm), os.environ.get("PATH", "")])


zustand = pytest.mark.skipif(
    not (ZUSTAND / "node_modules").is_dir()
    or shutil.which("node", path=node_path()) is None,
    reason="prepared zustand install or node not available",
)


@zustand
def test_real_vitest_scratch_module_on_zustand(tmp_path):
    repo = tmp_path / "zustand"
    subprocess.run(
        ["git", "clone", "--shared", "--quiet", str(ZUSTAND), str(repo)], check=True
    )
    link_dependencies(ZUSTAND, repo)
    toolchain = detect(repo, [{"path": "src/vanilla.ts"}], node_path())
    assert (toolchain.runner, toolchain.test_dir, toolchain.test_suffix) == (
        "vitest",
        "tests",
        ".test.tsx",
    )
    assert toolchain.typescript and toolchain.globals
    assert "import { create } from 'zustand'" in toolchain.import_sample
    tools = Workspace(repo, env=dict(os.environ, PATH=node_path()))
    code = (
        "import { createStore } from 'zustand/vanilla'\n\n"
        "describe('store', () => {\n"
        "  it('reads', () => {\n"
        "    expect(createStore(() => ({ a: 1 })).getState()).toEqual({ a: 1 })\n"
        "  })\n"
        "  it('is wrong on purpose', () => {\n"
        "    expect(createStore(() => ({ a: 1 })).getState()).toEqual({ a: 2 })\n"
        "  })\n"
        "})\n"
    )
    started = time.perf_counter()
    result = run_test_module(tools, toolchain, code, 60)
    assert time.perf_counter() - started < 60
    assert not result.ok
    record = result.meta["record"]
    assert record["tests"] == {
        "store > reads": "passed",
        "store > is wrong on purpose": "failed",
    }
    assert record["failures"][0]["line"] == 8
    assert "FAIL store > is wrong on purpose" in result.text
    assert '"a": 2' in result.text  # the diff from the default reporter
    assert "at check line 8" in result.text
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "__devstill_check" not in status


@zustand
def test_real_vitest_checks_need_expects_ignore_coverage_and_drop_frames(tmp_path):
    repo = tmp_path / "zustand"
    subprocess.run(
        ["git", "clone", "--shared", "--quiet", str(ZUSTAND), str(repo)], check=True
    )
    link_dependencies(ZUSTAND, repo)
    config = repo / "vitest.config.mts"
    text = config.read_text()
    assert "provider: 'v8'," in text
    # A project-wide coverage threshold one scratch module can never meet.
    config.write_text(
        text.replace(
            "provider: 'v8',",
            "provider: 'v8', enabled: true, thresholds: { lines: 90 },",
        )
    )
    toolchain = detect(repo, [{"path": "src/vanilla.ts"}], node_path())
    tools = Workspace(repo, env=dict(os.environ, PATH=node_path()))
    passing = (
        "import { createStore } from 'zustand/vanilla'\n\n"
        "it('reads', () => {\n"
        "  expect(createStore(() => ({ a: 1 })).getState()).toEqual({ a: 1 })\n"
        "})\n"
    )
    result = run_test_module(tools, toolchain, passing, 60)
    assert result.ok and result.text.endswith("1 passed"), result.text
    assert result.meta["record"]["assertions_required"]
    silent = (
        "import { createStore } from 'zustand/vanilla'\n\n"
        "it('never asserts', () => {\n"
        "  const store = createStore(() => ({ a: 1 }))\n"
        "  store.subscribe((state) => { expect(state.a).toBe(2) })\n"
        "})\n"
    )
    result = run_test_module(tools, toolchain, silent, 60)
    assert not result.ok and "No expect ran in this test" in result.text
    react = (
        "import { render, screen } from '@testing-library/react'\n\n"
        "it('renders', () => {\n"
        "  render(<div>count: 5</div>)\n"
        "  expect(screen.getByText('count: 6')).toBeTruthy()\n"
        "})\n"
    )
    result = run_test_module(tools, toolchain, react, 60)
    assert not result.ok and "Unable to find an element" in result.text
    assert "❯" not in result.text and "node_modules" not in result.text
    assert "at check line 5" in result.text


@zustand
def test_real_guard_reports_regressions_and_type_errors_on_zustand(tmp_path):
    def clone(target):
        subprocess.run(
            ["git", "clone", "--shared", "--quiet", str(ZUSTAND), str(target)],
            check=True,
        )
        shutil.rmtree(target / ".git")
        link_dependencies(ZUSTAND, target)
        return target

    work = clone(tmp_path / "work")
    toolchain = detect(work, [{"path": "src/vanilla/shallow.ts"}], node_path())
    tools = Workspace(work, env=dict(os.environ, PATH=node_path()))
    guard = JsGuard(tools, toolchain, originals_from(ZUSTAND))
    try:
        guard.start(clone(tmp_path / "pristine"))
        tools.edit(
            "src/vanilla/shallow.ts",
            [
                {
                    "oldText": "    return compareIterables(valueA, valueB)",
                    "newText": "    return false",
                },
                {
                    "oldText": "  if (Object.is(valueA, valueB)) {",
                    "newText": "  const unused: number = 'x'\n  if (Object.is(valueA, valueB)) {",
                },
            ],
        )
        guard.changed()
        found = guard.regressions(60)
        assert (
            "tests/vanilla/shallow.test.tsx > generators > pure iterable"
            in (found["regressions"])
        )
        # The upstream test that already fails here is never blamed.
        assert not any("devtools" in name for name in found["regressions"])
        assert len(found["type_errors"]) == 1
        assert "TS2322" in found["type_errors"][0]
        upstream = (
            "tests/devtools.test.tsx > When state changes with automatic setter "
            "inferring... > sends { type: setStateName || 'setCount`, ...rest } as "
            "the action with current state"
        )
        assert guard.known_failures([upstream], 30) == [upstream]
    finally:
        guard.cleanup()
