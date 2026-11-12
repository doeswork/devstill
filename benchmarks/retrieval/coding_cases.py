"""Source-authored coding cases. References/checks are evaluator-only data.

These are proposed changes to pinned projects, not upstream bug claims. Cases
are grouped by subsystem family; neither repetitions nor assertions are cases.
"""

from __future__ import annotations

W = "src/werkzeug/"
U = "crates/cargo-util/src/"
P = "crates/cargo-platform/src/lib.rs"
CASES = []


def case(id, corpus, workload, query, checks, edits, *, file=None, package=None):
    CASES.append(
        {
            "id": id,
            "corpus": corpus,
            "workload": workload,
            "family": id,
            "query": query,
            "checks": checks,
            "edits": edits,
            "check_file": file,
            "package": package,
        }
    )


def edit(path, old, new):
    return {"path": path, "old": old, "new": new}


case(
    "w_repr",
    "werkzeug",
    "small",
    "When no browser was parsed, repr(UserAgent) should be <UserAgent> (or the subclass name), without None/None. Preserve the existing parsed-browser representation.",
    "from werkzeug.user_agent import UserAgent\nu=UserAgent('raw'); assert repr(u)=='<UserAgent>'; u.browser='B'; u.version='1'; assert repr(u)=='<UserAgent B/1>'",
    [
        edit(
            W + "user_agent.py",
            '        return f"<{type(self).__name__} {self.browser}/{self.version}>"',
            '        if self.browser is None:\n            return f"<{type(self).__name__}>"\n        return f"<{type(self).__name__} {self.browser}/{self.version}>"',
        )
    ],
)
case(
    "w_salt",
    "werkzeug",
    "small",
    "Make gen_salt reject bool lengths with TypeError, while preserving positive integer lengths and the existing ValueError for zero.",
    "from werkzeug.security import gen_salt\nassert len(gen_salt(7))==7\nfor value in (True,False):\n try: gen_salt(value)\n except TypeError: pass\n else: raise AssertionError('bool accepted')\ntry: gen_salt(0)\nexcept ValueError: pass\nelse: raise AssertionError('zero accepted')",
    [
        edit(
            W + "security.py",
            "    if length <= 0:",
            '    if isinstance(length, bool):\n        raise TypeError("length must be an integer, not bool")\n    if length <= 0:',
        )
    ],
)
case(
    "w_header_lines",
    "werkzeug",
    "small",
    "quote_header_value must raise ValueError for CR or LF anywhere in the stringified value. Keep quoting and escaping behavior for ordinary values.",
    "from werkzeug.http import quote_header_value\nassert quote_header_value('abc')=='abc'; assert quote_header_value('a b')=='\"a b\"'\nfor v in ('a\\nb','a\\rb'):\n try: quote_header_value(v)\n except ValueError: pass\n else: raise AssertionError('newline accepted')",
    [
        edit(
            W + "http.py",
            "    value_str = str(value)\n",
            '    value_str = str(value)\n    if "\\r" in value_str or "\\n" in value_str:\n        raise ValueError("header value contains newline")\n',
        )
    ],
)
case(
    "w_close",
    "werkzeug",
    "small",
    "Make ClosingIterator.close idempotent: its registered cleanup callbacks should run at most once even when close is called twice. Preserve callback order.",
    "from werkzeug.wsgi import ClosingIterator\nseen=[]; it=ClosingIterator([], [lambda:seen.append(1),lambda:seen.append(2)]); it.close(); it.close(); assert seen==[1,2]",
    [
        edit(
            W + "wsgi.py",
            "        for callback in self._callbacks:\n            callback()",
            "        callbacks, self._callbacks = self._callbacks, []\n        for callback in callbacks:\n            callback()",
        )
    ],
)
case(
    "w_age",
    "werkzeug",
    "small",
    "dump_age should reject bool with TypeError. Preserve None, integers and timedelta handling, including rejection of negative ages.",
    "from werkzeug.http import dump_age\nfrom datetime import timedelta\nassert dump_age(None) is None; assert dump_age(2)=='2'; assert dump_age(timedelta(seconds=3))=='3'\nfor v in (True,False):\n try: dump_age(v)\n except TypeError: pass\n else: raise AssertionError('bool accepted')",
    [
        edit(
            W + "http.py",
            "    if age is None:\n        return None\n    if isinstance(age, timedelta):",
            '    if isinstance(age, bool):\n        raise TypeError("age must not be bool")\n    if age is None:\n        return None\n    if isinstance(age, timedelta):',
        )
    ],
)
case(
    "w_description",
    "werkzeug",
    "small",
    "Normalize CRLF and lone CR to LF in HTTPException.get_description before HTML escaping and line-break rendering. Keep ordinary HTML escaping.",
    "from werkzeug.exceptions import BadRequest\nassert BadRequest('a\\r\\nb\\rc').get_description()=='<p>a<br>b<br>c</p>'; assert BadRequest('<x>').get_description()=='<p>&lt;x&gt;</p>'",
    [
        edit(
            W + "exceptions.py",
            '        description = escape(description).replace("\\n", Markup("<br>"))',
            '        description = description.replace("\\r\\n", "\\n").replace("\\r", "\\n")\n        description = escape(description).replace("\\n", Markup("<br>"))',
        )
    ],
)

case(
    "w_user_agent_info",
    "werkzeug",
    "feature",
    "Add UserAgent.to_dict returning string, platform, browser, version and language, preserving None. Expose Request.user_agent_info as a property delegating to the configured user_agent_class instance. Preserve user_agent and header behavior.",
    "from werkzeug.user_agent import UserAgent\nfrom werkzeug.wrappers import Request\nu=UserAgent('raw'); assert u.to_dict()==dict(string='raw',platform=None,browser=None,version=None,language=None)\nclass Custom(UserAgent):\n browser='custom'\nclass R(Request):\n user_agent_class=Custom\nr=R.from_values(headers={'User-Agent':'test'}); assert r.user_agent_info['browser']=='custom'; assert r.user_agent_info['string']=='test'; assert r.user_agent.to_header()=='test'",
    [
        edit(
            W + "user_agent.py",
            "    def to_header(self)",
            '    def to_dict(self):\n        return {key: getattr(self, key) for key in ("string", "platform", "browser", "version", "language")}\n\n    def to_header(self)',
        ),
        edit(
            W + "wrappers/request.py",
            "    def __init__(\n",
            "    @property\n    def user_agent_info(self):\n        return self.user_agent.to_dict()\n\n    def __init__(\n",
        ),
    ],
)
case(
    "w_json_errors",
    "werkzeug",
    "feature",
    "Add HTTPException.get_json_response(environ=None, scope=None), returning a Werkzeug Response with the exception status and JSON keys code, name and description. Preserve subclass get_headers headers such as Allow; replace Content-Type with application/json. Existing get_response stays HTML.",
    "from werkzeug.exceptions import BadRequest, MethodNotAllowed\nb=BadRequest('bad <value>'); r=b.get_json_response(); assert r.status_code==400; assert r.mimetype=='application/json'; assert r.json==dict(code=400,name='Bad Request',description='bad <value>'); assert b.get_response().mimetype=='text/html'\nr=MethodNotAllowed(valid_methods=['GET']).get_json_response(); assert r.headers['Allow']=='GET'",
    [
        edit(
            W + "exceptions.py",
            "    def get_body(\n",
            '    def get_json_response(self, environ=None, scope=None):\n        import json\n        from .wrappers import Response\n        headers = [(k,v) for k,v in self.get_headers(environ, scope) if k.lower() != "content-type"]\n        return Response(json.dumps(dict(code=self.code, name=self.name, description=self.description)), status=self.code, headers=headers, mimetype="application/json")\n\n    def get_body(\n',
        )
    ],
)
case(
    "w_context_iterator",
    "werkzeug",
    "feature",
    "Support using ClosingIterator as a context manager. Enter returns the iterator; exit runs cleanup on normal and exceptional exit and does not suppress exceptions. Iteration behavior stays unchanged.",
    "from werkzeug.wsgi import ClosingIterator\nseen=[]\nwith ClosingIterator([b'x'],lambda:seen.append(1)) as it: assert list(it)==[b'x']\ntry:\n with ClosingIterator([],lambda:seen.append(2)): raise RuntimeError('expected')\nexcept RuntimeError: pass\nelse: raise AssertionError('suppressed')\nassert seen==[1,2]",
    [
        edit(
            W + "wsgi.py",
            "    def __iter__(self) -> ClosingIterator:",
            "    def __enter__(self):\n        return self\n\n    def __exit__(self, exc_type, exc_value, traceback):\n        self.close()\n        return False\n\n    def __iter__(self) -> ClosingIterator:",
        )
    ],
)
case(
    "w_json_sequence",
    "werkzeug",
    "feature",
    "Add Request.is_json_sequence recognizing the application/json-seq mimetype, including content-type parameters. Independently add Response.json_sequence(values) as a classmethod producing application/json-seq: each JSON-encoded value prefixed with ASCII RS and suffixed with LF. Empty input produces empty bytes. Preserve normal JSON APIs.",
    "from werkzeug.wrappers import Request,Response\nimport json\nassert Request.from_values(content_type='application/json-seq; charset=utf-8').is_json_sequence; assert not Request.from_values(content_type='application/json').is_json_sequence\nr=Response.json_sequence([{'a':1},2]); assert r.mimetype=='application/json-seq'; assert [json.loads(v) for v in r.data.decode().split('\\x1e')[1:]]==[{'a':1},2]; assert Response.json_sequence([]).data==b''",
    [
        edit(
            W + "wrappers/request.py",
            "    def __init__(\n",
            '    @property\n    def is_json_sequence(self):\n        return self.mimetype == "application/json-seq"\n\n    def __init__(\n',
        ),
        edit(
            W + "wrappers/response.py",
            "    def __init__(\n",
            '    @classmethod\n    def json_sequence(cls, values):\n        import json\n        return cls("".join("\\x1e" + json.dumps(v) + "\\n" for v in values), mimetype="application/json-seq")\n\n    def __init__(\n',
        ),
    ],
)
case(
    "w_vary_middleware",
    "werkzeug",
    "feature",
    "Create werkzeug.middleware.vary.VaryMiddleware(app, names). Append configured names to response Vary headers with case-insensitive deduplication, preserving existing order and then configured order. Preserve status, other headers, iterable and start_response exc_info. Empty names leave headers unchanged.",
    "from werkzeug.middleware.vary import VaryMiddleware\ndef app(env,start):\n start('200 OK',[('Vary','Accept'),('X-Test','yes')]); return [b'ok']\nseen=[]\ndef start(status,headers,exc_info=None): seen.append((status,dict(headers))); return lambda b:None\nassert list(VaryMiddleware(app,['accept','Origin'])({},start))==[b'ok']; assert seen[0][0]=='200 OK'; assert seen[0][1]['Vary']=='Accept, Origin'; assert seen[0][1]['X-Test']=='yes'",
    [
        edit(
            W + "middleware/vary.py",
            None,
            'class VaryMiddleware:\n    def __init__(self, app, names):\n        self.app, self.names = app, tuple(names)\n    def __call__(self, environ, start_response):\n        def start(status, headers, exc_info=None):\n            if not self.names:\n                return start_response(status, headers, exc_info)\n            values = [v.strip() for k,h in headers if k.lower()=="vary" for v in h.split(",") if v.strip()]\n            result=[]\n            seen=set()\n            for value in [*values,*self.names]:\n                if value.lower() not in seen:\n                    seen.add(value.lower()); result.append(value)\n            headers=[(k,v) for k,v in headers if k.lower()!="vary"]\n            headers.append(("Vary", ", ".join(result)))\n            return start_response(status,headers,exc_info)\n        return self.app(environ,start)\n',
        )
    ],
)
case(
    "w_drop_headers",
    "werkzeug",
    "feature",
    "Create werkzeug.middleware.drop_headers.DropHeadersMiddleware(app, names), filtering matching response header names case-insensitively, including duplicate headers. Preserve remaining order, status, body iterable, write callable and exc_info forwarding.",
    "from werkzeug.middleware.drop_headers import DropHeadersMiddleware\nseen=[]; write=lambda b:None\ndef start(status,headers,exc_info=None): seen.append((status,headers,exc_info)); return write\ndef app(env,start):\n assert start('201 Created',[('X-Secret','a'),('Keep','b'),('x-secret','c')],None) is write; return [b'ok']\nassert list(DropHeadersMiddleware(app,['x-secret'])({},start))==[b'ok']; assert seen==[('201 Created',[('Keep','b')],None)]",
    [
        edit(
            W + "middleware/drop_headers.py",
            None,
            "class DropHeadersMiddleware:\n    def __init__(self, app, names):\n        self.app=app\n        self.names={n.lower() for n in names}\n    def __call__(self, environ, start_response):\n        def start(status, headers, exc_info=None):\n            return start_response(status, [(k,v) for k,v in headers if k.lower() not in self.names], exc_info)\n        return self.app(environ,start)\n",
        )
    ],
)

case(
    "c_empty_platform",
    "cargo",
    "small",
    "Reject an empty named Platform during FromStr parsing with ParseError, while preserving named targets and cfg parsing.",
    'assert!("".parse::<Platform>().is_err()); assert!("x86_64-unknown-linux-gnu".parse::<Platform>().is_ok()); assert!("cfg(unix)".parse::<Platform>().is_ok());',
    [
        edit(
            P,
            "    fn validate_named_platform(name: &str) -> Result<(), ParseError> {",
            '    fn validate_named_platform(name: &str) -> Result<(), ParseError> {\n        if name.is_empty() { return Err(ParseError::new(name, ParseErrorKind::InvalidTarget("empty target".into()))); }',
        )
    ],
    file=P,
    package="cargo-platform",
)
case(
    "c_empty_dependency",
    "cargo",
    "small",
    "Make cargo_util::registry::make_dep_path return an empty String for an empty dependency name in both prefix modes, without panicking or changing valid names.",
    'assert_eq!(make_dep_path("",false), ""); assert_eq!(make_dep_path("",true), ""); assert_eq!(make_dep_path("Serde",false), "Se/rd/Serde");',
    [
        edit(
            U + "registry.rs",
            "    match dep_name.len() {",
            "    match dep_name.len() {\n        0 => String::new(),",
        )
    ],
    file=U + "registry.rs",
    package="cargo-util",
)
case(
    "c_ci_empty",
    "cargo",
    "small",
    "is_ci should require a nonempty CI or TF_BUILD environment value. Empty values should not identify CI; a nonempty value such as false still counts as present.",
    'std::env::set_var("CI", ""); std::env::set_var("TF_BUILD", ""); assert!(!is_ci()); std::env::set_var("CI", "false"); assert!(is_ci()); std::env::remove_var("CI"); std::env::remove_var("TF_BUILD");',
    [
        edit(
            U + "lib.rs",
            '    std::env::var("CI").is_ok() || std::env::var("TF_BUILD").is_ok()',
            '    ["CI", "TF_BUILD"].iter().any(|k| std::env::var(k).map(|v| !v.is_empty()).unwrap_or(false))',
        )
    ],
    file=U + "lib.rs",
    package="cargo-util",
)
case(
    "c_parent_paths",
    "cargo",
    "small",
    "normalize_path should preserve leading parent components on relative paths, including ../../a. Continue collapsing a/../b and never ascend above an absolute root.",
    'assert_eq!(normalize_path(Path::new("../../a")), PathBuf::from("../../a")); assert_eq!(normalize_path(Path::new("a/../b")),PathBuf::from("b")); assert_eq!(normalize_path(Path::new("/../a")),PathBuf::from("/a"));',
    [
        edit(
            U + "paths.rs",
            "            Component::ParentDir => {\n                ret.pop();\n            }",
            '            Component::ParentDir => {\n                if ret.file_name().is_some_and(|n| n != "..") { ret.pop(); }\n                else if !ret.has_root() { ret.push(".."); }\n            }',
        )
    ],
    file=U + "paths.rs",
    package="cargo-util",
)
case(
    "c_exit_codes",
    "cargo",
    "small",
    "Change is_simple_exit_code to classify the full unsigned byte range 0 through 255 as simple, while keeping negative and larger values abnormal.",
    "assert!(is_simple_exit_code(255)); assert!(is_simple_exit_code(0)); assert!(!is_simple_exit_code(-1)); assert!(!is_simple_exit_code(256));",
    [
        edit(
            U + "process_error.rs",
            "    code >= 0 && code <= 127",
            "    code >= 0 && code <= 255",
        )
    ],
    file=U + "process_error.rs",
    package="cargo-util",
)
case(
    "c_program_empty",
    "cargo",
    "small",
    "ProcessBuilder.program must panic with a clear message on an empty executable name, instead of storing it. Preserve ordinary program replacement and chainability.",
    'let mut p=ProcessBuilder::new("echo"); p.program("printf"); assert_eq!(p.get_program(),"printf"); assert!(std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| { p.program(""); })).is_err());',
    [
        edit(
            U + "process_builder.rs",
            "        self.program = program.as_ref().to_os_string();",
            '        assert!(!program.as_ref().is_empty(), "program must not be empty");\n        self.program = program.as_ref().to_os_string();',
        )
    ],
    file=U + "process_builder.rs",
    package="cargo-util",
)

case(
    "c_digest",
    "cargo",
    "feature",
    "Add Sha256::digest_hex(bytes: &[u8]) -> String for one-shot hashing without a caller-owned hasher. Expose cargo_util::sha256_hex with the same signature delegating to it. Preserve incremental hashing.",
    'assert_eq!(sha256_hex(b"abc"),"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"); assert_eq!(Sha256::digest_hex(b"abc"),sha256_hex(b"abc")); assert_eq!(Sha256::new().update(b"a").update(b"bc").finish_hex(),sha256_hex(b"abc"));',
    [
        edit(
            U + "sha256.rs",
            "impl Sha256 {",
            "impl Sha256 {\n    pub fn digest_hex(bytes: &[u8]) -> String { Self::new().update(bytes).finish_hex() }",
        ),
        edit(
            U + "lib.rs",
            None,
            "\npub fn sha256_hex(bytes: &[u8]) -> String { Sha256::digest_hex(bytes) }\n",
        ),
    ],
    file=U + "lib.rs",
    package="cargo-util",
)
case(
    "c_optional_read",
    "cargo",
    "feature",
    "Add paths::read_optional(path: &Path) -> anyhow::Result<Option<String>>. Missing file returns None, readable UTF-8 returns Some, and other errors propagate. Re-export it at cargo_util::read_optional.",
    'let d=tempfile::tempdir().unwrap(); let p=d.path().join("missing"); assert_eq!(read_optional(&p).unwrap(),None); std::fs::write(&p,"hello").unwrap(); assert_eq!(read_optional(&p).unwrap(),Some("hello".into())); assert!(read_optional(d.path()).is_err());',
    [
        edit(
            U + "paths.rs",
            None,
            "\npub fn read_optional(path: &Path) -> Result<Option<String>> { match std::fs::read_to_string(path) { Ok(s)=>Ok(Some(s)), Err(e) if e.kind()==std::io::ErrorKind::NotFound=>Ok(None), Err(e)=>Err(e.into()) } }\n",
        ),
        edit(U + "lib.rs", None, "\npub use paths::read_optional;\n"),
    ],
    file=U + "lib.rs",
    package="cargo-util",
)
case(
    "c_registry_url",
    "cargo",
    "feature",
    "Add registry::make_dep_url(base: &str, name: &str) -> String, trimming trailing slashes from base and using the lowercase dependency name with make_dep_path. Re-export at cargo_util::make_dep_url. Preserve existing make_dep_path case behavior.",
    'assert_eq!(make_dep_url("https://example.test/index///","Serde"),"https://example.test/index/se/rd/serde"); assert_eq!(registry::make_dep_path("AbCd",false),"Ab/Cd/AbCd");',
    [
        edit(
            U + "registry.rs",
            None,
            "\npub fn make_dep_url(base: &str, name: &str) -> String { format!(\"{}/{}\",base.trim_end_matches('/'),make_dep_path(&name.to_lowercase(),false)) }\n",
        ),
        edit(U + "lib.rs", None, "\npub use registry::make_dep_url;\n"),
    ],
    file=U + "lib.rs",
    package="cargo-util",
)
case(
    "c_bulk_env",
    "cargo",
    "feature",
    "Add chainable ProcessBuilder.envs(&[(&str, &str)]) -> &mut Self. Apply entries in order using existing env semantics, so the last duplicate wins and unrelated settings remain.",
    'let mut p=ProcessBuilder::new("echo"); p.env("KEEP","yes").envs(&[("X","1"),("X","2"),("Y","3")]).arg("ok"); assert_eq!(p.get_env("X").unwrap(),"2"); assert_eq!(p.get_env("KEEP").unwrap(),"yes"); assert_eq!(p.get_env("Y").unwrap(),"3");',
    [
        edit(
            U + "process_builder.rs",
            "impl ProcessBuilder {",
            "impl ProcessBuilder {\n    pub fn envs(&mut self, values: &[(&str, &str)]) -> &mut Self { for (key,value) in values { self.env(key,value); } self }",
        )
    ],
    file=U + "process_builder.rs",
    package="cargo-util",
)
case(
    "c_platform_batch",
    "cargo",
    "feature",
    "Add public parse_platforms(values: &[&str]) -> Result<Vec<Platform>, ParseError> preserving order and returning the first parse error. Add Platform.matches_any(targets: &[(&str, &[Cfg])]) -> bool, true if any pair matches, false for empty input.",
    'let p=parse_platforms(&["x86_64-unknown-linux-gnu","cfg(unix)"]).unwrap(); assert_eq!(p.len(),2); assert!(parse_platforms(&["cfg("]).is_err()); assert!(p[1].matches_any(&[("other",&[Cfg::Name("unix".into())]) ])); assert!(!p[0].matches_any(&[]));',
    [
        edit(
            P,
            "impl Platform {",
            "impl Platform {\n    pub fn matches_any(&self, targets: &[(&str, &[Cfg])]) -> bool { targets.iter().any(|(name,cfg)| self.matches(name,cfg)) }",
        ),
        edit(
            P,
            None,
            "\npub fn parse_platforms(values: &[&str]) -> Result<Vec<Platform>, ParseError> { values.iter().map(|s| s.parse()).collect() }\n",
        ),
    ],
    file=P,
    package="cargo-platform",
)
case(
    "c_text_module",
    "cargo",
    "feature",
    "Add a cargo-util text module with normalize_line_endings(text: &str) -> String converting CRLF to LF while preserving lone CR. Re-export the function from cargo_util. Preserve Unicode and existing trailing newlines.",
    'assert_eq!(normalize_line_endings("a\r\nb\rc\n"),"a\nb\rc\n"); assert_eq!(normalize_line_endings("é\r\n"),"é\n"); assert_eq!(normalize_line_endings(""),"");',
    [
        edit(
            U + "text.rs",
            None,
            'pub fn normalize_line_endings(text: &str) -> String { text.replace("\\r\\n", "\\n") }\n',
        ),
        edit(
            U + "lib.rs",
            None,
            "\npub mod text;\npub use text::normalize_line_endings;\n",
        ),
    ],
    file=U + "lib.rs",
    package="cargo-util",
)

# A family is the target subsystem, not merely a unique query identifier.
for c in CASES:
    c["family"] = c["corpus"] + ":" + c["edits"][0]["path"]
assert len(CASES) == 24

for c in CASES:
    if c["corpus"] == "cargo":
        c["checks"] = c["checks"].replace("\r", "\\r")
