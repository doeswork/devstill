from __future__ import annotations

from pathlib import Path

from devstill.parsers.python_ast import parse_python


def test_python_boundaries_decorators_nesting_and_comments(tmp_path: Path) -> None:
    source = '''# module heading

# describes the class
@register("account")
class Account:
    """User account."""

    # checks a password
    async def authenticate(self, password: str) -> bool:
        def normalize(value: str) -> str:
            return value.strip()
        return normalize(password) == "secret"

def neighboring():
    return None
'''
    path = tmp_path / "accounts.py"
    path.write_text(source, encoding="utf-8")

    result = parse_python(path, tmp_path)
    by_name = {chunk.qualname: chunk for chunk in result.chunks if chunk.qualname}

    account = by_name["Account"]
    authenticate = by_name["Account.authenticate"]
    normalize = by_name["Account.authenticate.normalize"]
    neighbor = by_name["neighboring"]
    assert account.start_line == 4
    assert account.leading_comments == "# describes the class"
    assert account.code.startswith('@register("account")')
    assert "def neighboring" not in account.code
    assert authenticate.kind == "async_method"
    assert authenticate.leading_comments == "    # checks a password"
    assert normalize.kind == "nested_function"
    assert "def neighboring" not in normalize.code
    assert neighbor.start_line == 14


def test_multiline_assignment_unicode_and_syntax_error(tmp_path: Path) -> None:
    valid = tmp_path / "valid.py"
    valid.write_text('GREETING = (\n    "héllo"\n)\n', encoding="utf-8")
    chunks = parse_python(valid, tmp_path).chunks
    constant = next(chunk for chunk in chunks if chunk.kind == "constant")
    assert constant.code == 'GREETING = (\n    "héllo"\n)'
    invalid = tmp_path / "invalid.py"
    invalid.write_text("def broken(:\n", encoding="utf-8")
    result = parse_python(invalid, tmp_path)
    assert not result.chunks
    assert result.errors[0].line == 1


def test_utf8_byte_columns_preserve_exact_single_line_source(tmp_path: Path) -> None:
    path = tmp_path / "unicode.py"
    path.write_text('GREETING = "héllo"\n', encoding="utf-8")
    constant = next(
        chunk
        for chunk in parse_python(path, tmp_path).chunks
        if chunk.kind == "constant"
    )
    assert constant.code == 'GREETING = "héllo"'
