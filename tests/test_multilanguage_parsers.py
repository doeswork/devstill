from __future__ import annotations

from pathlib import Path

import pytest

from devstill.parsers import parse_source_file, parser_version_for
from devstill.parsers.registry import parser_spec_for


@pytest.mark.parametrize(
    ("filename", "source", "expected"),
    [
        (
            "main.go",
            (
                "package main\n"
                'import "fmt"\n'
                "type Greeter interface { Greet() string }\n"
                "type User struct { Name string }\n"
                "const Limit = 5\n"
                "var Default = User{}\n"
                "func NewUser() User { return User{} }\n"
                'func (u *User) Greet() string { return "héllo " + u.Name }\n'
            ),
            {
                ("import_group", "imports@2"),
                ("interface", "Greeter"),
                ("struct", "User"),
                ("constant", "Limit"),
                ("variable", "Default"),
                ("function", "NewUser"),
                ("method", "User.Greet"),
            },
        ),
        (
            "lib.rs",
            (
                "use std::fmt;\n"
                "mod auth { pub fn check() {} }\n"
                "struct User { name: String }\n"
                "enum State { Ready }\n"
                "trait Login { fn login(&self); }\n"
                "impl User { fn login(&self) {} }\n"
                "const LIMIT: usize = 5;\n"
                "static READY: bool = true;\n"
                "macro_rules! greet { () => {}; }\n"
                "fn main() {}\n"
            ),
            {
                ("use", "use@1"),
                ("module", "auth"),
                ("function", "auth.check"),
                ("struct", "User"),
                ("enum", "State"),
                ("trait", "Login"),
                ("impl", "User"),
                ("method", "User.login"),
                ("constant", "LIMIT"),
                ("static", "READY"),
                ("macro", "greet"),
                ("function", "main"),
            },
        ),
        (
            "accounts.rb",
            (
                'require "json"\n'
                "module Accounts\n"
                "  LIMIT = 5\n"
                "  SIGN_IN = ->(user) { user.login }\n"
                "  class User\n"
                "    def login\n"
                "      true\n"
                "    end\n"
                "    def self.find(id)\n"
                "      id\n"
                "    end\n"
                "  end\n"
                "end\n"
            ),
            {
                ("require", "require@1"),
                ("module", "Accounts"),
                ("constant", "Accounts.LIMIT"),
                ("lambda", "Accounts.SIGN_IN"),
                ("class", "Accounts.User"),
                ("method", "Accounts.User.login"),
                ("singleton_method", "Accounts.User.find"),
            },
        ),
        (
            "schema.sql",
            (
                "CREATE TABLE users (id INTEGER);\n"
                "CREATE VIEW active_users AS SELECT * FROM users;\n"
                "CREATE INDEX users_id ON users(id);\n"
                "CREATE FUNCTION find_user() RETURNS INTEGER AS 'SELECT 1';\n"
                "SELECT * FROM users;\n"
            ),
            {
                ("table", "users"),
                ("view", "active_users"),
                ("index", "users_id"),
                ("function", "find_user"),
                ("query", "select@5"),
            },
        ),
        (
            "build.sh",
            (
                "#!/usr/bin/env bash\n"
                "NAME=devstill\n"
                "build() {\n"
                '  echo "$NAME"\n'
                "}\n"
                "echo ready\n"
            ),
            {
                ("assignment", "NAME"),
                ("function", "build"),
                ("command_group", "commands@6"),
            },
        ),
        (
            "page.html",
            (
                '<main id="app">\n'
                '  <section id="account">\n'
                '    <form id="login"><input name="email"></form>\n'
                "  </section>\n"
                "</main>\n"
            ),
            {
                ("main", "app"),
                ("section", "app.account"),
                ("form", "app.account.login"),
            },
        ),
        (
            "site.css",
            (
                "body, .app { color: red; }\n"
                "@media screen { .small { display: none; } }\n"
                "@keyframes fade { from { opacity: 0; } to { opacity: 1; } }\n"
            ),
            {
                ("selector", "body, .app"),
                ("at_rule", "@media screen"),
                ("keyframes", "@keyframes fade"),
            },
        ),
        (
            "User.java",
            (
                "package app;\n"
                "import java.util.List;\n"
                "class User {\n"
                "  private final int id = 1;\n"
                "  User() {}\n"
                "  void login() {}\n"
                "  record Session(String token) {}\n"
                "}\n"
            ),
            {
                ("package", "app"),
                ("import", "import@2"),
                ("class", "User"),
                ("field", "User.id"),
                ("constructor", "User.User"),
                ("method", "User.login"),
                ("record", "User.Session"),
            },
        ),
    ],
)
def test_native_structural_parsers(
    tmp_path: Path,
    filename: str,
    source: str,
    expected: set[tuple[str, str | None]],
) -> None:
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")

    first = parse_source_file(path, tmp_path)
    second = parse_source_file(path, tmp_path)

    assert not first.errors
    assert first.quality is not None
    assert first.quality.status == "GOOD"
    actual = {
        (chunk.kind, chunk.qualname)
        for chunk in first.chunks
        if chunk.parser_metadata.get("searchable", True)
    }
    assert expected <= actual
    assert [chunk.id for chunk in first.chunks] == [chunk.id for chunk in second.chunks]
    for chunk in first.chunks:
        assert chunk.code in source
    if filename == "site.css":
        media = next(chunk for chunk in first.chunks if chunk.name == "@media screen")
        assert ".small { display: none; }" in media.code


@pytest.mark.parametrize(
    ("filename", "source"),
    [
        ("bad.go", "package main\nfunc broken( {\n"),
        ("bad.rs", "fn broken( {\n"),
        ("bad.rb", "def broken(\nend\n"),
        ("bad.sql", "CREATE TABLE users (\n"),
        ("bad.sh", "if true; then\n"),
        ("bad.html", "<main><form></main>\n"),
        ("bad.css", ".x { color: red;\n"),
        ("Bad.java", "class Bad { void broken( { }\n"),
    ],
)
def test_native_parser_syntax_errors_recover_as_exact_meh_text(
    tmp_path: Path, filename: str, source: str
) -> None:
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")

    result = parse_source_file(path, tmp_path)

    assert result.errors
    assert result.chunks[0].code == source
    assert result.chunks[0].parser_metadata["searchable"] is False
    searchable = [
        chunk
        for chunk in result.chunks
        if chunk.parser_metadata.get("searchable", True)
    ]
    assert searchable
    assert all(chunk.code in source for chunk in searchable)
    assert all(chunk.parser_metadata["fallback"] for chunk in searchable)
    assert result.quality is not None
    assert result.quality.status == "MEH"
    assert "exact bounded fallback used" in result.quality.reasons[-1]


def test_django_template_failure_uses_template_aware_exact_fallback(
    tmp_path: Path,
) -> None:
    path = tmp_path / "conditional.html"
    source = (
        '{% extends "base.html" %}\n'
        "{% block content %}\n"
        "{% if active %}<main>{% else %}<section>{% endif %}x</main>\n"
        "{% endblock %}\n"
        "<hr>\n"
        "{% macro card(item) %}\n"
        "<article>{{ item }}</article>\n"
        "{% endmacro %}\n"
    )
    path.write_text(source, encoding="utf-8")

    result = parse_source_file(path, tmp_path)

    assert result.errors
    assert result.quality is not None
    assert result.quality.status == "MEH"
    assert result.chunks[0].code == source
    searchable = [
        chunk
        for chunk in result.chunks
        if chunk.parser_metadata.get("searchable", True)
    ]
    assert [(chunk.kind, chunk.name) for chunk in searchable] == [
        ("template_section", "preamble"),
        ("template_block", "content"),
        ("template_section", "section@5"),
        ("template_block", "card"),
    ]
    assert "".join(chunk.code for chunk in searchable) == source
    assert all(chunk.parser_metadata["template_aware"] for chunk in searchable)
    assert [chunk.parser_metadata["template_kind"] for chunk in searchable] == [
        "section",
        "block",
        "section",
        "macro",
    ]
    assert all(
        chunk.parser_metadata["structural_fallback"] == "tree_sitter_html"
        for chunk in searchable
    )
    assert "exact template-aware fallback used" in result.quality.reasons[-1]


def test_registry_routes_basenames_and_shebangs(tmp_path: Path) -> None:
    gemfile = tmp_path / "Gemfile"
    gemfile.write_text('source "https://rubygems.org"\n', encoding="utf-8")
    script = tmp_path / "deploy"
    script.write_text("#!/usr/bin/env bash\ndeploy() { echo ok; }\n", encoding="utf-8")

    assert parser_spec_for(gemfile).language == "ruby"
    assert parser_spec_for(script).language == "bash"
    assert "ruby" in parser_version_for(gemfile)
    assert "bash" in parser_version_for(script)
    assert any(
        chunk.name == "deploy" for chunk in parse_source_file(script, tmp_path).chunks
    )


def test_native_invalid_utf8_is_bad_not_binary(tmp_path: Path) -> None:
    path = tmp_path / "invalid.go"
    path.write_bytes(b"package main\n// \xff\n")

    result = parse_source_file(path, tmp_path)

    assert result.errors
    assert result.quality is not None
    assert result.quality.status == "BAD"
    assert "UTF-8" in result.errors[0].message


def test_rust_attributes_and_java_annotations_stay_with_definitions(
    tmp_path: Path,
) -> None:
    rust = tmp_path / "model.rs"
    rust.write_text(
        "#[derive(Debug)]\npub struct User { id: u64 }\n",
        encoding="utf-8",
    )
    java = tmp_path / "User.java"
    java.write_text(
        "@Deprecated\npublic class User {}\n",
        encoding="utf-8",
    )

    rust_user = next(
        chunk
        for chunk in parse_source_file(rust, tmp_path).chunks
        if chunk.name == "User" and chunk.kind == "struct"
    )
    java_user = next(
        chunk
        for chunk in parse_source_file(java, tmp_path).chunks
        if chunk.name == "User" and chunk.kind == "class"
    )

    assert rust_user.start_line == 1
    assert rust_user.code.startswith("#[derive(Debug)]\n")
    assert rust_user.parser_metadata["attributes"] == ["#[derive(Debug)]"]
    assert java_user.start_line == 1
    assert java_user.code.startswith("@Deprecated\n")
