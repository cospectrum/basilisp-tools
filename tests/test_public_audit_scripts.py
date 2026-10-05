"""Regression coverage for copying untrusted public-project checkouts."""

import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "public_formatter_audit",
    Path(__file__).resolve().parents[1] / "scripts" / "check_public_format.py",
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def make_link(path, target):
    try:
        path.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as error:
        pytest.skip(f"Symlinks are unavailable: {error}")


def test_public_checkout_copy_ignores_external_and_dangling_links(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "main.lpy").write_text("(ns main)\n")
    outside = tmp_path / "outside.json"
    outside.write_text("private content")
    make_link(checkout / "external.json", outside)
    make_link(checkout / "dangling.json", tmp_path / "missing.json")
    target = tmp_path / "copy"

    assert audit.copy_checkout(checkout, target) == [
        "dangling.json", "external.json",
    ]
    assert (target / "main.lpy").read_text() == "(ns main)\n"
    assert not (target / "external.json").exists()
    assert not (target / "dangling.json").is_symlink()
    assert outside.read_text() == "private content"


def test_selected_source_links_are_rejected_even_through_a_parent(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "main.lpy").write_text("(ns main)\n")
    make_link(checkout / "linked.lpy", outside / "main.lpy")
    make_link(checkout / "linked-dir", outside)
    make_link(checkout / "missing.lpy", outside / "missing.lpy")

    assert audit.source_symlinks(
        checkout, ["linked.lpy", "linked-dir/main.lpy", "missing.lpy", "regular.lpy"]
    ) == ["linked.lpy", "linked-dir/main.lpy", "missing.lpy"]
