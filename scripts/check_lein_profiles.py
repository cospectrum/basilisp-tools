"""Compare literal formatter profile merging with the installed Leiningen.

Run inside nix develop .#compatibility. Leiningen's version is pinned by flake.lock.
Only generated, literal fixtures are passed to its profile machinery.
"""

from __future__ import annotations

import argparse
import importlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


CASES = [
    ("default-dev", '{:profiles {:dev {:cljfmt {:indentation? false}}}}'),
    ("default-provided", '{:profiles {:provided {:cljfmt {:indentation? false}}}}'),
    ("default-order", '{:profiles {:provided {:cljfmt {:indentation? false}} :dev {:cljfmt {:indentation? true}}}}'),
    ("inactive-profile", '{:profiles {:release {:cljfmt {:indentation? false}}}}'),
    ("composite-default", '{:profiles {:default [:formatting] :formatting {:cljfmt {:indentation? false}} :dev {:cljfmt {:indentation? true}}}}'),
    ("keyword-alias", '{:profiles {:default :formatting :formatting {:cljfmt {:indentation? false}}}}'),
    ("nested-composite", '{:profiles {:default [:one :two] :one [:formatting] :formatting {:cljfmt {:indentation? false}} :two {:cljfmt {:indent-line-comments? true}}}}'),
    ("last-occurrence", '{:profiles {:default [:one :two :one] :one {:cljfmt {:indentation? false}} :two {:cljfmt {:indentation? true}}}}'),
    ("deep-map", '{:cljfmt {:extra-indents {alpha [[:inner 0]]}} :profiles {:dev {:cljfmt {:extra-indents {beta [[:block 1]]}}}}}'),
    ("sequence-append", '{:cljfmt {:extra-indents {alpha [[:inner 0]]}} :profiles {:dev {:cljfmt {:extra-indents {alpha [[:block 1]]}}}}}'),
    ("replace-right", '{:cljfmt {:extra-indents {alpha [[:inner 0]]}} :profiles {:dev {:cljfmt {:extra-indents ^:replace {beta [[:block 1]]}}}}}'),
    ("replace-left", '{:cljfmt {:extra-indents ^:replace {alpha [[:inner 0]]}} :profiles {:dev {:cljfmt {:extra-indents {beta [[:block 1]]}}}}}'),
    ("displace-right", '{:cljfmt {:extra-indents {alpha [[:inner 0]]}} :profiles {:dev {:cljfmt {:extra-indents ^:displace {beta [[:block 1]]}}}}}'),
    ("displace-left", '{:cljfmt {:extra-indents ^:displace {alpha [[:inner 0]]}} :profiles {:dev {:cljfmt {:extra-indents {beta [[:block 1]]}}}}}'),
    ("prepend", '{:cljfmt {:extra-indents {alpha [[:inner 0]]}} :profiles {:dev {:cljfmt {:extra-indents {alpha ^:prepend [[:block 1]]}}}}}'),
    ("paths-default", '{:profiles {:dev {:source-paths ["generated"] :test-paths ["integration"]}}}'),
    ("paths-prepend", '{:source-paths ["src"] :test-paths ["test"] :profiles {:dev {:source-paths ["generated"] :test-paths ["integration"]}}}'),
    ("paths-replace", '{:source-paths ["src"] :profiles {:dev {:source-paths ^:replace ["generated"]}}}'),
    ("paths-displace", '{:source-paths ["src"] :profiles {:dev {:source-paths ^:displace ["generated"]}}}'),
    ("nil-does-not-replace", '{:cljfmt {:indentation? false} :profiles {:dev {:cljfmt nil}}}'),
    ("set-union", '{:cljfmt {:aligned-forms {let #{0}}} :profiles {:dev {:cljfmt {:aligned-forms {let #{1}}}}}}'),
    ("inline-composite-map", '{:profiles {:default [:first {:cljfmt {:indentation? true}}] :first {:cljfmt {:indentation? false}}}}'),
    ("composite-displace", '{:cljfmt {:indentation? true} :profiles {:default ^:displace [:one] :one {:cljfmt {:indentation? false}}}}'),
    ("nested-composite-displace", '{:cljfmt {:indentation? true} :profiles {:default [:middle] :middle ^:displace [:one] :one {:cljfmt {:indentation? false}}}}'),
]


def discover_jar() -> Path:
    executable = shutil.which("lein")
    if not executable:
        raise RuntimeError("Leiningen is required; enter nix develop .#compatibility.")
    prefix = Path(executable).resolve().parent.parent
    jars = list(prefix.glob("**/leiningen*.jar"))
    if len(jars) != 1:
        raise RuntimeError(f"Pass --lein-jar; found {len(jars)} jars inside {prefix}.")
    return jars[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lein-jar", type=Path)
    args = parser.parse_args()
    jar = args.lein_jar or discover_jar()
    import basilisp_tools  # noqa: F401
    from basilisp.lang.keyword import keyword as kw
    from basilisp.lang.map import map as lmap
    from basilisp.lang.vector import vector

    formatter = importlib.import_module("basilisp_tools.format")
    edn = importlib.import_module("basilisp.edn")
    core = importlib.import_module("basilisp.core")
    failures = []
    with tempfile.TemporaryDirectory(prefix="blt-lein-") as temporary:
        directory = Path(temporary)
        cases = []
        for name, source in CASES:
            root = directory / name
            root.mkdir()
            for path in ["src", "test", "generated", "integration"]:
                (root / path).mkdir()
            (root / "project.clj").write_text(
                '(defproject oracle "0" ' + source[1:-1] + ')', encoding="utf-8"
            )
            cases.append(lmap({kw("name"): name, kw("source"): source, kw("root"): str(root)}))
        # Project-local definitions override inline definitions.
        local_case = cases[0].assoc(kw("name"), "local-profiles")
        local_root = directory / "local-profiles"
        shutil.copytree(directory / "default-dev", local_root)
        local_text = '{:dev {:cljfmt {:indentation? true}}}'
        (local_root / "profiles.clj").write_text(local_text, encoding="utf-8")
        cases.append(local_case.assoc(kw("root"), str(local_root), kw("local"), local_text))
        selected = cases[3].assoc(
            kw("name"), "explicit-selection",
            kw("profiles"), vector([kw("release")]),
        )
        cases.append(selected)
        inputs, output = directory / "cases.edn", directory / "results.edn"
        inputs.write_text(core.pr_str(vector(cases)), encoding="utf-8")
        subprocess.run([
            "java", "-cp", str(jar), "clojure.main",
            str(Path(__file__).with_suffix(".clj").resolve()), str(inputs), str(output),
        ], check=True)
        results = edn.read_string(output.read_text(encoding="utf-8"))
        for case, expected in zip(cases, results, strict=True):
            name = case.val_at(kw("name"))
            root = Path(case.val_at(kw("root")))
            selection = case.val_at(kw("profiles"))
            options = lmap({kw("lein-profiles"): selection}) if selection else lmap({})
            try:
                actual = formatter.load_config(str(root), options)
                if expected.val_at(kw("error")) is not None:
                    failures.append({"case": name, "error": "Expected Lein rejection", "actual": str(actual)})
                    continue
                expected_options = expected.val_at(kw("cljfmt"))
                expected_paths = [
                    str(Path(path).relative_to(root))
                    for key in ["source-paths", "test-paths"]
                    for path in expected.val_at(kw(key))
                ]
                actual_options = actual.dissoc(kw("paths"), kw("project-root"))
                if actual_options != expected_options or list(actual.val_at(kw("paths"))) != expected_paths:
                    failures.append({"case": name, "actual": str(actual),
                                     "expected": str(expected)})
            except Exception as error:
                if expected.val_at(kw("error")) is None:
                    failures.append({"case": name, "error": str(error)})
    print(f"Lein profile comparisons: {len(cases) - len(failures)}/{len(cases)}")
    for failure in failures:
        print(json.dumps(failure))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
