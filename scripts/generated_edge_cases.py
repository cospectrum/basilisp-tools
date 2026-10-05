"""Runtime-valid edge projects for the expanded generated-project audit."""

from textwrap import dedent


def source(text):
    return dedent(text).lstrip()


def cases(case_type):
    yield case_type(
        "mixed-source-tree",
        {
            "demo/constants.lpy": "(ns demo.constants)\n(def base 30)\n",
            "demo/shared.cljc": source("""
                (ns demo.shared (:require [demo.constants :as constants]))
                #?(:clj (defn value [] (JavaOnly/missing))
                   :lpy (defn value [] constants/base))
            """),
            "demo/versioned.cljc": source("""
                (ns demo.versioned)
                (do #?@(:lpy39- [(defn offset [] (unavailable-python-feature))]
                        :lpy310+ [(defn offset [] 70)]))
            """),
            "demo/main.lpy": source("""
                (ns demo.main (:require [demo.shared :as shared]
                                       [demo.versioned :as versioned]))
                (defn run [] (+ (shared/value) (versioned/offset)))
            """),
        },
        "100",
        "(shared/value 1)",
        "invalid-arity",
    )

    yield case_type(
        "typed-package",
        {
            "support/__init__.py": (
                "from .models import Box, NamedReader, choose, read_text\n"
            ),
            "support/models.py": source("""
                from typing import Generic, Protocol, TypeVar, overload
                T = TypeVar('T')
                class Box(Generic[T]):
                    def __init__(self, value: T) -> None:
                        self.value = value
                    def get(self) -> T:
                        return self.value
                class Reader(Protocol):
                    def read(self) -> str: ...
                class NamedReader:
                    def __init__(self, text: str) -> None:
                        self.text = text
                    def read(self) -> str:
                        return self.text
                def read_text(reader: Reader) -> str:
                    return reader.read()
                @overload
                def choose(value: str) -> int: ...
                @overload
                def choose(value: int) -> str: ...
                def choose(value: str | int) -> int | str:
                    return len(value) if isinstance(value, str) else str(value)
            """),
            "demo/main.lpy": source("""
                (ns demo.main (:import [support :as py]))
                (defn run []
                  (let [box (py/Box 40)
                        reader (py/NamedReader "abc")
                        text (py/read-text reader)]
                    (+ (.get box) (py/choose "hello") (count text) 52)))
            """),
        },
        "100",
        "(py/read-text 1)",
        "type-mismatch",
        python_probes=(("demo/main.lpy", "py/choose"), ("demo/main.lpy", "py/Box")),
    )

    yield case_type(
        "typed-stub-package",
        {
            "support.py": source("""
                class TextBox:
                    def __init__(self, value): self.value = value
                    def get(self): return self.value
                class NamedReader:
                    def __init__(self, text): self.text = text
                    def read(self): return self.text
                def read_text(reader): return reader.read()
                def choose(value):
                    return len(value) if isinstance(value, str) else str(value)
            """),
            "support.pyi": source("""
                from typing import Generic, Protocol, TypeVar, overload
                T = TypeVar('T')
                class Box(Generic[T]):
                    def __init__(self, value: T) -> None: ...
                    def get(self) -> T: ...
                class TextBox(Box[str]): ...
                class Reader(Protocol):
                    def read(self) -> str: ...
                class NamedReader:
                    def __init__(self, text: str) -> None: ...
                    def read(self) -> str: ...
                def read_text(reader: Reader) -> str: ...
                @overload
                def choose(value: str) -> int: ...
                @overload
                def choose(value: int) -> str: ...
            """),
            "demo/main.lpy": source("""
                (ns demo.main (:import [support :as py]))
                (defn run []
                  (let [box (py/TextBox "abc")
                        reader (py/NamedReader "hello")]
                    (+ (count (.get box)) (count (py/choose 40))
                       (count (py/read-text reader)) 90)))
            """),
        },
        "100",
        "(py/choose nil)",
        "type-mismatch",
        python_probes=(("demo/main.lpy", "py/TextBox"), ("demo/main.lpy", "py/choose")),
    )

    yield case_type(
        "reader-data-unicode",
        {
            "demo/main.lpy": source(r"""
                (ns demo.main)
                (def ^:private payload
                  [#uuid "12345678-1234-5678-1234-567812345678"
                   #inst "2026-10-05T01:02:03.000Z"
                   #b"\x00\xff" #py [1 2] #py {"name" "λ"}
                   ##Inf ##-Inf ##NaN \newline \u03bb 1/2 2M])
                (defn run []
                  (let [значение 40 mapping #:sample{:n 46}
                        empty-name :ns/ numeric-name :123
                        pattern #"λ\s+λ"]
                    (+ значение (:sample/n mapping) (count payload)
                       (count [empty-name numeric-name])
                       (if (re-find pattern "λ λ") 0 1))))
            """),
        },
        "100",
        '(do "😀λ" generated-missing)',
        "unresolved-symbol",
    )

    count = 1000
    definitions = "".join(f"(defn value-{i:04d} [] {i})\n" for i in range(count))
    calls = " ".join(f"(value-{i:04d})" for i in range(count))
    yield case_type(
        "single-file-1000-definitions",
        {
            "demo/main.lpy": (
                "(ns demo.main)\n" + definitions + f"(defn run [] (+ {calls}))\n"
            ),
        },
        str(sum(range(count))),
        "(value-0000 1)",
        "invalid-arity",
    )

    yield case_type(
        "native-backslash-names",
        {
            "demo/main.lpy": (
                "(ns demo.main)\n"
                "(defn value\\path [x] (+ x 1))\n"
                "(defn run []\n"
                "(let [payload {:a\\b 98}]\n"
                "(+ (:a\\b payload) (value\\path 1))))\n"
            ),
        },
        "100",
        "(value\\path)",
        "invalid-arity",
    )
    yield case_type(
        "native-keyword-quote-boundaries",
        {
            "demo/main.lpy": (
                "(ns demo.main)\n(defn run []\n(+ 96 (count [:'a :ns/'b])))\n"
            ),
        },
        "100",
        "(run 1)",
        "invalid-arity",
    )
