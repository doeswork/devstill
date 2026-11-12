import pytest

from devstill.runtime.context import support_records
from devstill.runtime.editor import editor_messages, source_path


def context(repo, imports, annotation="Registry", *, exclude=None):
    root = repo / "src" / "sample"
    root.mkdir(parents=True, exist_ok=True)
    (root / "__init__.py").write_text("")
    text = (
        imports
        + f"""\nclass Child:
    def __init__(self, registry: {annotation}):
        self.registry = registry
    def render(self, value):
        return str(value)
"""
    )
    (root / "child.py").write_text(text)
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines, 1) if "def render" in line)

    def guard(name):
        if exclude and name.endswith(exclude):
            raise ValueError("Excluded")
        return source_path(repo, name)

    records = [
        {
            "path": "src/sample/child.py",
            "lines": [start, start + 1],
            "code": "\n".join(lines[start - 1 : start + 1]),
        }
    ]
    support = support_records(repo, records, {"exports": [], "modules": []}, guard)
    # Every excerpt is exact source; derived import hints are separate metadata.
    for record in support:
        a, b = record["lines"]
        assert record["code"] == "\n".join(
            (repo / record["path"]).read_text().splitlines()[a - 1 : b]
        )
    assert sum(len(r["code"]) + len(r.get("import_hint", "")) for r in support) <= 16000
    return support


def registry(repo):
    root = repo / "src" / "sample"
    root.mkdir(parents=True, exist_ok=True)
    (
        root / "registry.py"
    ).write_text("""raise RuntimeError("retrieval must never import this module")
class Registry:
    def __init__(
        self,
        entries=None,
        *, strict=True,
    ):
        self.entries = entries
""")
    return root


@pytest.mark.parametrize(
    "header",
    [
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:",
        "from typing import TYPE_CHECKING as TC\nif TC:",
        "import typing\nif typing.TYPE_CHECKING:",
        "import typing as t\nif t.TYPE_CHECKING:",
    ],
)
def test_type_checking_constructor_import_and_signature(tmp_path, header):
    registry(tmp_path)
    support = context(tmp_path, header + "\n    from .registry import Registry\n")
    assert any(r["kind"] == "type_checking_import" for r in support)
    assert [r["import_hint"] for r in support if "import_hint" in r] == [
        "from sample.registry import Registry"
    ]
    signature = next(r for r in support if r["kind"] == "constructor_signature")
    assert "entries=None" in signature["code"] and "strict=True" in signature["code"]
    assert "self.entries" not in signature["code"]
    assert (
        "from sample.registry import Registry"
        in editor_messages("change render", support)[1]["content"]
    )


def test_import_and_string_annotation_aliases(tmp_path):
    registry(tmp_path)
    support = context(
        tmp_path,
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from .registry import Registry as Catalog\n",
        '"Catalog"',
    )
    assert any(
        r.get("import_hint") == "from sample.registry import Registry as Catalog"
        for r in support
    )


@pytest.mark.parametrize(
    "mode", ["excluded", "missing", "ambiguous", "not_type_checking"]
)
def test_no_guessed_hint_for_unavailable_or_ambiguous_dependencies(tmp_path, mode):
    registry(tmp_path)
    header = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:"
    if mode == "missing":
        (tmp_path / "src/sample/registry.py").unlink()
    if mode == "ambiguous":
        other = tmp_path / "sample"
        other.mkdir()
        (other / "__init__.py").write_text("")
        (other / "registry.py").write_text("class Registry: pass\n")
    if mode == "not_type_checking":
        header = "TYPE_CHECKING = False\nif TYPE_CHECKING:"
    support = context(
        tmp_path,
        header + "\n    from .registry import Registry\n",
        exclude="registry.py" if mode == "excluded" else None,
    )
    assert not any("import_hint" in r for r in support)
    assert not any(r["path"].endswith("registry.py") for r in support)


def test_constructor_dependencies_are_two_targets_and_one_hop(tmp_path):
    root = registry(tmp_path)
    (root / "registry.py").write_text(
        "from .hidden import Hidden\nclass Registry:\n    def __init__(self, hidden: Hidden):\n        pass\n"
    )
    for name in ("Second", "Third", "Hidden"):
        (root / (name.lower() + ".py")).write_text(
            f"class {name}:\n    def __init__(self, value=None):\n        pass\n"
        )
    support = context(
        tmp_path,
        "from .registry import Registry\nfrom .second import Second\nfrom .third import Third\n",
        "tuple[Registry, Second, Third]",
    )
    assert len([r for r in support if "import_hint" in r]) == 2
    assert not any(r["path"].endswith(("third.py", "hidden.py")) for r in support)


def test_dependency_on_inherited_constructor_uses_its_own_imports(tmp_path):
    root = registry(tmp_path)
    (root / "base.py").write_text(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from .registry import Registry\nclass Base:\n    def __init__(self, registry: Registry):\n        pass\n"
    )
    (root / "__init__.py").write_text("")
    (root / "child.py").write_text(
        "from .base import Base\nclass Child(Base):\n    def render(self, value):\n        return str(value)\n"
    )
    support = support_records(
        tmp_path,
        [{"path": "src/sample/child.py", "lines": [3, 4]}],
        {"exports": [], "modules": []},
        lambda n: source_path(tmp_path, n),
    )
    assert any(
        r.get("import_hint") == "from sample.registry import Registry" for r in support
    )


def test_unrelated_type_checking_import_is_not_expanded(tmp_path):
    registry(tmp_path)
    support = context(
        tmp_path,
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from .registry import Registry\n",
        "str",
    )
    assert not any(
        r["kind"] in {"dependency_class", "type_checking_import"} for r in support
    )
