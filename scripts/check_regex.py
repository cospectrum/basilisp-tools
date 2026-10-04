"""Compare supported Java configuration patterns with the JDK's Pattern."""
from __future__ import annotations

import base64
import importlib
import itertools
import json
import subprocess
from pathlib import Path

PATTERNS = [
    r"^foo.*", r"\.lpy$", r"\Qfoo.bar\E", r"\Q[foo]\E", r"\Qfoo",
    r"\\Qfoo\\E", r"(?<word>[a-z]+)-\k<word>",
    r"\p{L}+", r"\P{L}+", r"\p{IsLatin}+", r"\p{InGreek}+", r"\p{javaLowerCase}+",
    r"\p{javaUpperCase}+", r"\p{javaDigit}+", r"\p{javaLetterOrDigit}+",
    r"\p{javaWhitespace}+", r"\p{javaSpaceChar}+", r"\p{javaJavaIdentifierStart}+",
    r"\p{javaJavaIdentifierPart}+", r"\p{javaIdentifierIgnorable}+",
    r"\p{Lower}+", r"\p{Upper}+", r"\p{Alpha}+", r"\p{Alnum}+", r"\p{XDigit}+",
    r"\p{Punct}+", r"\p{Graph}+", r"\p{Print}+", r"\p{Space}+",
    r"[a-z&&[^aeiou]]+", r"[\p{L}&&[^\p{Lu}]]+", r"[a-d[m-p]]+",
    r"a++a", r"(?>a+)a", r"(?<=foo.*)bar",
    r"\d+", r"\D+", r"\w+", r"\W+", r"\s+", r"\S+",
    r"(?U)\w+", r"(?U:\w+)-\w+", r"(?U)\w+(?-U:\w+)",
    r"\h+", r"\H+", r"\v+", r"\V+", r"\R", r"\z", r"\Z", r".$", r"(?s).+",
    r"(?d).+", r"(?d).$", r"(?m)^foo$", r"(?dm)^foo$",
    r"(?i)foo", r"(?iu)é", r"(?i:foo)bar", r"(?x) foo [ ] bar",
    r"(?i)é", r"(?i)[a-z]+", r"(?i)[^a-z]+", r"(?i)k", r"(?i)s", r"(?i)i",
    r"(?i)\p{Lu}+", r"(?i)\p{Lower}+", r"(?U)\p{Lower}+", r"(?U)\p{XDigit}+",
    r"(?i)\QFoo.bar\E", r"(?iu)\Qé\E", r"(?x)[a b]", "(?x)[a\tb]",
    r"(?m)^$", r"(?m)^", r"(?m)$",
    r"\e", r"\cA", r"\x{1F600}", r"\N{LATIN CAPITAL LETTER A}", r"\0123",
    r"(?i)\u0061", r"(?i)[\Qabc\E]", r"(?i)[A-z]", r"\xA", r"\uabc",
]
TEXTS = [
    "", "foo", "foo.bar", "[foo]", "foobar", "foofoo", "foo-foo", "foo-bar",
    "a", "aa", "aaa", "bcd", "aeiou", "mnop", "abc123", "_x9", "$name",
    "foo bar", "FOO", "FOObar", "FOOBAR", "é", "É", "λ", "Λ", "αβ", "１２", "١٢",
    "abc\n", "foo\n", "foo\r", "foo\r\n", "foo\u0085", "foo\u2028", "foo\u2029",
    "one\rfoo\rtwo", "one\nfoo\ntwo", "one\u2028foo\u2029two",
    " ", "\t", "\n", "\r\n", "\v", "\f", "\u0085", "\u00a0", "\u180e",
    "\u2000", "\u2007", "\u2028", "\u202f", "\u3000", "\x00", "\x1c", "\u200b",
    "!@#$%^&*()", "\\Qfoo\\E", "ı", "ſ", "K", "ẞ", "ß", "Ｆ", "Éé", "FOO.BAR", "😀", "\x1b", "\x01", "S", "A", "ABC", "[",
]


def encode(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def main() -> int:
    import basilisp_tools  # noqa: F401
    adapter = importlib.import_module("basilisp_tools.regex")
    cases = list(itertools.product(PATTERNS, TEXTS))
    payload = "".join(f"{encode(pattern)}\t{encode(text)}\n" for pattern, text in cases)
    result = subprocess.run(
        ["java", "--source", "17", str(Path(__file__).with_suffix(".java"))],
        input=payload, text=True, capture_output=True, check=True,
    )
    lines = result.stdout.splitlines()
    if len(lines) != len(cases):
        raise RuntimeError("JDK returned an incomplete regex result.")
    failures = []
    for (pattern, text), line in zip(cases, lines, strict=True):
        expected = line.split("\t")
        try:
            compiled = adapter.compile_(pattern)
            found = adapter.search(compiled, text)
            actual = ["1" if adapter.fullmatch(compiled, text) else "0",
                      "1" if found else "0", encode(found.group()) if found else ""]
            if actual != expected:
                failures.append({"pattern": pattern, "text": text, "expected": expected, "actual": actual})
        except Exception as error:
            if expected[0] != "error":
                failures.append({"pattern": pattern, "text": text, "error": str(error)})
    print(f"Java regex comparisons: {len(cases) - len(failures)}/{len(cases)}")
    for failure in failures[:30]:
        print(json.dumps(failure, ensure_ascii=False))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
