"""Read pinned upstream fixtures without importing or executing their Python code.

Adapters retain complete fixture sources and provenance. Unsupported environments
remain inventory entries; they are never silently dropped from coverage totals.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
import tarfile
import urllib.request


def fetch_source(entry, corpus):
    """Fetch one immutable source archive, extracting only regular fixture files."""
    destination = corpus / entry["directory"]
    marker = destination / ".blt-source-revision"
    if marker.is_file() and marker.read_text().strip() == entry["revision"]:
        return
    archive = corpus / (entry["directory"] + ".tar.gz")
    corpus.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        url = f"https://codeload.github.com/{entry['repo']}/tar.gz/{entry['revision']}"
        request = urllib.request.Request(url, headers={"User-Agent": "basilisp-tools-typing-audit"})
        with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    with tarfile.open(archive) as bundle:
        for member in bundle:
            pieces = Path(member.name).parts
            if not pieces or not pieces[0].endswith("-" + entry["revision"]):
                raise ValueError(f"Archive root does not match pinned revision: {member.name}")
            relative = Path(*pieces[1:])
            if not member.isfile() or not relative.parts:
                continue
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Unsafe fixture archive path: {member.name}")
            if not any(relative.as_posix() == path.rstrip("/") or relative.as_posix().startswith(path)
                       for path in entry["paths"]):
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as source:
                target.write_bytes(source.read())
    marker.write_text(entry["revision"] + "\n")


def fixture(provider, path, name, files, metadata=None):
    payload = json.dumps(files, sort_keys=True).encode()
    return {
        "provider": provider, "path": path, "name": name, "files": files,
        "source_sha256": hashlib.sha256(payload).hexdigest(),
        "metadata": metadata or {},
    }


def python_files(provider, root, subdirectory):
    base = root / subdirectory
    for path in sorted(base.rglob("*.py")):
        yield fixture(provider, path.relative_to(root).as_posix(), path.relative_to(base).as_posix(),
                      {path.name: path.read_text(encoding="utf-8")},
                      {"import_root": str(path.parent), "source_file": path.name})


def mypy_fixtures(root):
    for path in sorted((root / "test-data/unit").glob("check-*.test")):
        source = path.read_text(encoding="utf-8")
        cases = list(re.finditer(r"^\[case ([^\]]+)\]\s*$", source, re.M))
        for index, match in enumerate(cases):
            body = source[match.end():cases[index + 1].start() if index + 1 < len(cases) else len(source)].lstrip("\n")
            files, metadata = {}, {"source_file": "main.py", "sections": []}
            sections = list(re.finditer(r"^\[([^\]]+)\]\s*$", body, re.M))
            files["main.py"] = body[:sections[0].start()] if sections else body
            for number, section in enumerate(sections):
                label = section.group(1)
                content = body[section.end():sections[number + 1].start() if number + 1 < len(sections) else len(body)].lstrip("\n")
                metadata["sections"].append(label)
                if label.startswith("file "):
                    files[label[5:]] = content
                elif label == "out":
                    metadata["expected_output"] = content
            unsupported = [label for label in metadata["sections"]
                           if label.startswith(("builtins ", "typing ", "out", "delete ")) and label != "out"
                           or re.match(r"file .+\.\d+$", label)]
            if unsupported:
                metadata["unsupported_environment"] = "Custom builtins or incremental checker environment: " + ", ".join(unsupported)
            flags = re.findall(r"^#\s*(?:flags|cmd):\s*(.+)$", files["main.py"], re.M)
            configurations = [filename for filename in files if Path(filename).name in
                              {"mypy.ini", ".mypy.ini", "pyproject.toml", "setup.cfg"}]
            if flags or configurations:
                metadata.update(flags=flags, configuration_files=configurations)
                metadata["unsupported_environment"] = "Upstream mypy command flags/configuration are not reproduced"
            yield fixture("mypy", path.relative_to(root).as_posix(), match.group(1), files, metadata)


def rust_testcases(source):
    """Find testcase! macro boundaries while skipping Rust strings and comments."""
    trivia = r"(?:\s|//[^\n]*(?:\n|$)|/\*[\s\S]*?\*/)*"
    header = (r"\btestcase!\s*\(" + trivia
              + r'(?:bug\s*=\s*("(?:\\.|[^"\\])*")\s*,' + trivia + r")?(\w+)\s*,")
    for match in re.finditer(header, source):
        start = match.end()
        position, depth, raw = start, 1, []
        while position < len(source) and depth:
            raw_start = re.match(r'r(#{0,16})"', source[position:])
            if raw_start:
                end_marker = '"' + raw_start.group(1)
                content_start = position + raw_start.end()
                end = source.find(end_marker, content_start)
                if end < 0:
                    break
                raw.append((source[content_start:end], source[start:position]))
                position = end + len(end_marker)
            elif source.startswith("//", position):
                end = source.find("\n", position)
                position = end if end >= 0 else len(source)
            elif source.startswith("/*", position):
                end = source.find("*/", position + 2)
                position = end + 2 if end >= 0 else len(source)
            elif source[position] == '"':
                position += 1
                while position < len(source):
                    char = source[position]
                    position += 1
                    if char == "\\":
                        position += 1
                    elif char == '"':
                        break
            else:
                depth += (source[position] == "(") - (source[position] == ")")
                position += 1
        yield match.group(2), raw, source[start:position], depth == 0, match.group(1)


def rust_environment_supported(raw):
    if not raw:
        return False
    prefix = raw[-1][1]
    prefix = re.sub(r'r(#{0,16})"[\s\S]*?"\1', 'SOURCE', prefix)
    prefix = re.sub(r'"(?:\\.|[^"\\])*"', 'SOURCE', prefix)
    prefix = re.sub(r'//[^\n]*|/\*[\s\S]*?\*/', '', prefix)
    prefix = re.sub(r'\s+', '', prefix).rstrip(',')
    if not prefix:
        return True
    args = r"\(SOURCE,SOURCE(?:,SOURCE)?,?\)"
    pattern = r"(?:crate::test::util::)?TestEnv::(?:new\(\)|one(?:_with_path)?" + args + r")(?:\.add(?:_with_path)?" + args + r")*"
    return bool(re.fullmatch(pattern, prefix) and len(re.findall(r"(?:one|add)(?:_with_path)?\(", prefix)) == len(raw) - 1)


def pyrefly_fixtures(root):
    for path in sorted((root / "pyrefly/lib/test").rglob("*.rs")):
        for name, raw, body, complete, bug in rust_testcases(path.read_text(encoding="utf-8")):
            metadata = {"source_file": "main.py", "macro_complete": complete}
            files = {"main.py": raw[-1][0].lstrip("\n")} if raw else {}
            missing = []
            for index, (content, prefix) in enumerate(raw[:-1]):
                environment = re.search(r'(?:one|add|one_with_path|add_with_path)\(\s*"([^"\n]+)"\s*,\s*(?:"([^"\n]+)"\s*,\s*)?$', prefix)
                if environment:
                    module, filename = environment.groups()
                    files[filename or module.replace(".", "/") + ".py"] = content.lstrip("\n")
                else:
                    missing.append(index)
            if missing:
                metadata["unsupported_environment"] = f"Unmapped auxiliary Rust raw strings: {missing}"
            if not rust_environment_supported(raw):
                metadata["unsupported_environment"] = "Upstream Rust environment helper/configuration is not reproduced"
            if bug:
                metadata["upstream_bug"] = bug
                metadata["unsupported_environment"] = "Upstream testcase declares a known bug; its oracle needs individual review"
            if not complete or not raw:
                metadata["unsupported_environment"] = "testcase macro has no extractable complete Python source"
            if "PythonVersion::Python3_14" in body:
                metadata["python_version"] = "3.14"
            yield fixture("pyrefly", path.relative_to(root).as_posix(), name, files, metadata)


def ty_fixtures(root):
    base = root / "crates/ty_python_semantic/resources/mdtest"
    for path in sorted(base.rglob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        headings, sections, configurations = [], {}, {}
        index = 0
        while index < len(lines):
            line = lines[index]
            header = re.match(r"^(#{1,6}) (.+?)\s*$", line)
            if header:
                depth = len(header.group(1))
                headings = headings[:depth - 1] + [header.group(2)]
                index += 1
                continue
            fence = re.match(r"^(`{3,})(py|pyi|python|toml|ipynb)\s*$", line)
            if not fence:
                index += 1
                continue
            start = index
            index += 1
            content = []
            while index < len(lines) and not re.match(r"^" + re.escape(fence.group(1)) + r"\s*$", lines[index]):
                content.append(lines[index])
                index += 1
            key = tuple(headings)
            language = fence.group(2)
            if language == "toml":
                configurations.setdefault(key, []).append("".join(content))
            else:
                section = sections.setdefault(key, {"files": {}, "metadata": {"source_file": "mdtest_snippet.py"}})
                previous = start - 1
                while previous >= 0 and not lines[previous].strip():
                    previous -= 1
                named = re.match(r"^`([^`]+)`:\s*$", lines[previous]) if previous >= 0 else None
                filename = named.group(1) if named else "mdtest_snippet." + ("pyi" if language == "pyi" else "py")
                section["files"][filename] = section["files"].get(filename, "") + "".join(content) + "\n"
                if language == "ipynb":
                    section["metadata"]["unsupported_environment"] = "Notebook fixture requires notebook cell semantics"
            index += 1
        for headings, section in sections.items():
            configs = [value for key, values in configurations.items() if headings[:len(key)] == key for value in values]
            section["metadata"]["configuration"] = configs
            if configs:
                section["metadata"]["unsupported_environment"] = "Upstream environment/rule configuration is not yet reproduced by the interop adapter"
            if not section["metadata"]["source_file"] in section["files"]:
                section["metadata"]["source_file"] = next(iter(section["files"]))
            yield fixture("ty", path.relative_to(root).as_posix(), " / ".join(headings), section["files"], section["metadata"])


def fixtures(provider, root):
    if provider == "typing":
        yield from python_files(provider, root, "conformance/tests")
    elif provider == "pyright":
        yield from python_files(provider, root, "packages/pyright-internal/src/tests/samples")
    elif provider == "mypy":
        yield from mypy_fixtures(root)
    elif provider == "pyrefly":
        yield from pyrefly_fixtures(root)
    elif provider == "ty":
        yield from ty_fixtures(root)
    else:
        raise ValueError(f"Unknown upstream provider: {provider}")
