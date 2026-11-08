import json

import pytest

from devstill.runtime.context import support_records
from devstill.runtime.editor import source_path
from devstill.runtime.requirements import delivery_checks, delivery_requirements

pytest_plugins = ("tests.test_minimal_runtime",)


def package(repo):
    root = repo / "src" / "sample"
    root.mkdir(parents=True)
    (root / "__init__.py").write_text("")
    return root


def test_constructor_context_follows_same_file_and_imported_bases(tmp_path):
    root = package(tmp_path)
    (root / "base.py").write_text(
        "class Base:\n    def __init__(self, registry):\n        self.registry = registry\n"
    )
    text = "from .base import Base as Parent\nimport uuid\n\nclass Middle(Parent):\n    pass\n\nclass Child(Middle):\n    def render(self, value):\n        return str(value)\n"
    (root / "convert.py").write_text(text)
    records = [
        {
            "path": "src/sample/convert.py",
            "lines": [8, 9],
            "code": "    def render(self, value):\n        return str(value)",
        }
    ]
    support = support_records(
        tmp_path,
        records,
        {"exports": [], "modules": []},
        lambda n: source_path(tmp_path, n),
    )
    joined = "\n".join(r["code"] for r in support)
    assert "def __init__(self, registry)" in joined
    assert "class Child(Middle)" in joined
    assert "class Middle(Parent)" in joined
    assert "from .base import Base as Parent" in joined
    assert any(r["path"] == "src/sample/base.py" for r in support)
    assert sum(len(r["code"]) for r in support) <= 16000


def test_explicit_module_and_pronoun_export_are_runtime_checks(tmp_path):
    root = package(tmp_path)
    query = (
        "Create sample.header_names with normalize_name(value: str) -> str. "
        "Export the function from the sample package root."
    )
    req = delivery_requirements(tmp_path, query)
    assert req["modules"] == [
        {"module": "sample.header_names", "path": "src/sample/header_names.py"}
    ]
    assert req["exports"] == [
        {"module": "sample", "name": "normalize_name", "path": "src/sample/__init__.py"}
    ]
    assert delivery_checks(req)
    support = support_records(tmp_path, [], req, lambda n: source_path(tmp_path, n))
    assert any(r["path"] == "src/sample/__init__.py" for r in support)
    assert not (root / "header_names.py").exists()


def test_ambiguous_prose_is_not_a_guessed_delivery(tmp_path):
    package(tmp_path)
    assert delivery_requirements(
        tmp_path, "Explain sample.header_names and the export options"
    ) == {"modules": [], "exports": [], "files": []}


def test_replaced_failing_assertion_is_shown_in_review(session, monkeypatch):
    # The runtime cannot tell "wrong check" from "wrong patch" here (it mirrors a
    # correct patch with a wrong expectation). A replacement is therefore allowed
    # by default, but never silent: the review shows the retired assertion.
    from devstill.runtime.service import render

    actions = iter(
        [
            {
                "edits": [{"path": "a.py", "old": "x = 1", "new": "x = 2"}],
                "reads": [],
                "checks": [["python", "-c", "import a; assert a.x == 3"]],
            },
            {
                "edits": [],
                "reads": [],
                "checks": [["python", "-c", "import a; assert a.x == 2"]],
            },
        ]
    )
    monkeypatch.setattr(session, "generate", lambda *a: json.dumps(next(actions)))
    result = session.prepare("Set x to 3")
    assert result["status"] == "ready"
    assert result["execution"]["replaced_checks"] == [
        ["python", "-c", "import a; assert a.x == 3"]
    ]
    assert "Replaced failing check:\n```python\nimport a; assert a.x == 3" in render(
        result
    )
    assert (session.repo / "a.py").read_text() == "x = 1\n"  # Review, not applied.


def test_passing_check_is_rechecked_after_second_edit(session, monkeypatch):
    actions = iter(
        [
            {
                "edits": [{"path": "a.py", "old": "x = 1", "new": "x = 2"}],
                "reads": [],
                "checks": [
                    ["python", "-c", "import a; assert a.x == 2"],
                    ["python", "-c", "import a; assert a.x == 3"],
                ],
            },
            {
                "edits": [{"path": "a.py", "old": "x = 2", "new": "x = 3"}],
                "reads": [],
                "checks": [["python", "-c", "import a; assert a.x == 3"]],
            },
        ]
    )
    monkeypatch.setattr(session, "generate", lambda *a: json.dumps(next(actions)))
    result = session.prepare("Change x")
    assert result["status"] == "failed"


@pytest.mark.parametrize("omit", ["module", "export", "identity"])
def test_generated_checks_cannot_bypass_required_delivery(session, monkeypatch, omit):
    package(session.repo)
    edits = [{"path": "a.py", "old": "x = 1", "new": "x = 2"}]
    if omit != "module":
        edits.append(
            {
                "path": "src/sample/header_names.py",
                "old": "",
                "new": "def normalize_name(value: str) -> str:\n    return value.lower()\n",
            }
        )
    if omit != "export":
        edits.append(
            {
                "path": "src/sample/__init__.py",
                "old": "",
                "new": "def normalize_name(value):\n    return value.lower()\n",
            }
        )
        # Empty existing files must be updated using a real anchor; give one here.
        (session.repo / "src/sample/__init__.py").write_text("# exports\n")
        edits[-1]["old"] = "# exports\n"
    actions = iter(
        [
            {
                "edits": edits,
                "reads": [],
                "checks": [["python", "-c", "import a; assert a.x == 2"]],
            },
            {
                "edits": [],
                "reads": [],
                "checks": [["python", "-c", "import a; assert a.x == 2"]],
            },
        ]
    )
    monkeypatch.setattr(session, "generate", lambda *a: json.dumps(next(actions)))
    result = session.prepare(
        "Create sample.header_names with normalize_name(value: str) -> str. Export the function from the sample package root."
    )
    assert result["status"] == "failed"
    assert (session.repo / "a.py").read_text() == "x = 1\n"


def test_missing_export_is_completed_on_repair(session, monkeypatch):
    root = package(session.repo)
    (root / "__init__.py").write_text("# exports\n")
    actions = iter(
        [
            {
                "edits": [
                    {
                        "path": "src/sample/header_names.py",
                        "old": "",
                        "new": "def normalize_name(value: str) -> str:\n    return value.lower()\n",
                    }
                ],
                "reads": [],
                "checks": [
                    [
                        "python",
                        "-c",
                        "from sample.header_names import normalize_name; assert normalize_name('X') == 'x'",
                    ]
                ],
            },
            {
                "edits": [
                    {
                        "path": "src/sample/__init__.py",
                        "old": "# exports",
                        "new": "from .header_names import normalize_name as normalize_name",
                    }
                ],
                "reads": [],
                "checks": [
                    [
                        "python",
                        "-c",
                        "from sample import normalize_name; assert normalize_name('X') == 'x'",
                    ]
                ],
            },
        ]
    )
    monkeypatch.setattr(session, "generate", lambda *a: json.dumps(next(actions)))
    result = session.prepare(
        "Create sample.header_names with normalize_name(value: str) -> str. Export the function from the sample package root."
    )
    assert result["status"] == "ready", result
    assert not (root / "header_names.py").exists()
    session.apply(result["id"], result["digest"])
    assert (root / "header_names.py").is_file()


def test_explicit_file_location_is_not_treated_as_a_module(tmp_path):
    package(tmp_path)
    req = delivery_requirements(
        tmp_path, "Create src/sample/new.py and add a new file tests/test_new.py"
    )
    assert req["files"] == ["src/sample/new.py", "tests/test_new.py"]
    assert req["modules"] == []
    assert "src/sample/new.py" in delivery_checks(req)[0][2]


@pytest.mark.parametrize(
    "code",
    [
        "import sys",
        "class Check:\n def run(self):\n  assert False",
        "def test_x():\n assert False",
    ],
)
def test_setup_and_uncalled_checks_are_rejected(code):
    from devstill.runtime.editor import validate_checks

    with pytest.raises(ValueError, match="behavioral assertion"):
        validate_checks([["python", "-c", code]])


def test_exception_check_is_accepted():
    from devstill.runtime.editor import validate_checks

    validate_checks(
        [
            [
                "python",
                "-c",
                "import pytest\nwith pytest.raises(ValueError):\n int('bad')",
            ]
        ]
    )


def test_even_explicit_repair_cannot_retire_a_passing_check(session, monkeypatch):
    actions = iter(
        [
            {
                "edits": [{"path": "a.py", "old": "x = 1", "new": "x = 2"}],
                "reads": [],
                "checks": [
                    ["python", "-c", "import a; assert a.x == 2"],
                    ["python", "-c", "import a; assert a.x == 3"],
                ],
            },
            {
                "edits": [],
                "reads": [],
                "check_repairs": [
                    {
                        "index": 0,
                        "reason": "Retire the earlier passing requirement to change expectations",
                    }
                ],
                "checks": [["python", "-c", "import a; assert a.x == 2"]],
            },
        ]
    )
    monkeypatch.setattr(session, "generate", lambda *args: json.dumps(next(actions)))
    result = session.prepare("Change x")
    assert result["status"] == "failed"
    trace = (session.root / result["id"] / "editor-trace.json").read_text()
    assert "previously passing check must be preserved" in trace


def test_optional_base_context_respects_exclusions(tmp_path):
    root = package(tmp_path)
    (root / "base.py").write_text(
        "class Parent:\n    def __init__(self, secret):\n        pass\n"
    )
    (root / "child.py").write_text(
        "from .base import Parent\nclass Child(Parent):\n    def run(self):\n        return 1\n"
    )

    def guard(name):
        if name.endswith("base.py"):
            raise ValueError("Excluded source")
        return source_path(tmp_path, name)

    support = support_records(
        tmp_path,
        [{"path": "src/sample/child.py", "lines": [3, 4]}],
        {"modules": [], "exports": []},
        guard,
    )
    assert all(r["path"] != "src/sample/base.py" for r in support)
