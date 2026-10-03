"""Measure startup and warmed formatter performance on a fixed source corpus.

Run: uv run python scripts/benchmark.py src tests --repeat 3
Pass --profile /tmp/blt.prof to capture a cProfile of one formatting pass.
"""

import argparse
import cProfile
import importlib
from pathlib import Path
from statistics import median
from time import perf_counter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=["src", "tests"])
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--profile", type=Path)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")

    files = set()
    for name in args.paths:
        path = Path(name)
        if path.is_dir():
            files.update(p for p in path.rglob("*.lpy") if p.is_file())
        elif path.is_file():
            files.add(path)
        else:
            parser.error(f"No such file or directory: {path}")
    if not files:
        parser.error("No source files found")
    sources = [p.read_text(encoding="utf-8") for p in sorted(files)]

    started = perf_counter()
    import basilisp_tools  # noqa: F401 — initializes the Basilisp import hook
    from basilisp.lang.keyword import keyword

    syntax = importlib.import_module("basilisp_tools.syntax")
    formatter = importlib.import_module("basilisp_tools.format")
    print(f"Import/startup: {perf_counter() - started:.3f}s", flush=True)
    print(f"Corpus: {len(sources)} files, {sum(map(len, sources)):,} code points", flush=True)

    trees = [syntax.parse(source)[keyword("root")] for source in sources]
    for source, tree in zip(sources, trees):
        assert syntax.text(tree) == source, "Parser round-trip mismatch"
    # Warm all modules and caches before timing. Inputs remain unchanged in memory.
    expected = [formatter.format_string(source) for source in sources]

    def measure(label, function, inputs, outputs=None):
        samples = []
        for _ in range(args.repeat):
            started = perf_counter()
            result = [function(value) for value in inputs]
            samples.append(perf_counter() - started)
            if outputs is not None:
                assert result == outputs, f"Non-deterministic {label} output"
        print(f"{label}: median {median(samples):.3f}s; samples "
              + ", ".join(f"{value:.3f}s" for value in samples), flush=True)

    measure("Parse", syntax.parse, sources)
    measure("Reconstruct", syntax.text, trees, sources)
    measure("Format (includes parsing)", formatter.format_string, sources, expected)
    if args.profile:
        profile = cProfile.Profile()
        profile.runcall(lambda: [formatter.format_string(source) for source in sources])
        profile.dump_stats(str(args.profile))
        print(f"Profile: {args.profile}")


if __name__ == "__main__":
    main()
