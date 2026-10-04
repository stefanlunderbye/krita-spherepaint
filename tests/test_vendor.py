"""Choosing the bundled NumPy folder for the running Python and platform."""
import sys

import vendor


def test_tag_contains_python_version_platform_and_machine(monkeypatch):
    monkeypatch.setattr(vendor.sys, "platform", "win32")
    monkeypatch.setattr(vendor.platform, "machine", lambda: "AMD64")
    tag = vendor.vendor_tag()
    assert tag == f"cp{sys.version_info.major}{sys.version_info.minor}-win-x86_64"


def test_macos_arm_is_named_arm64(monkeypatch):
    monkeypatch.setattr(vendor.sys, "platform", "darwin")
    monkeypatch.setattr(vendor.platform, "machine", lambda: "arm64")
    assert vendor.vendor_tag().endswith("-macos-arm64")


def test_prefers_the_matching_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(vendor, "vendor_tag", lambda: "cp399-test-x86_64")
    (tmp_path / "_vendor" / "cp399-test-x86_64").mkdir(parents=True)
    monkeypatch.setattr(sys, "path", list(sys.path))
    chosen = vendor.add_vendor_path(str(tmp_path))
    assert chosen.endswith("cp399-test-x86_64")
    assert sys.path[0] == chosen


def test_falls_back_to_flat_vendor_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(vendor, "vendor_tag", lambda: "cp399-test-x86_64")
    (tmp_path / "_vendor").mkdir()
    monkeypatch.setattr(sys, "path", list(sys.path))
    chosen = vendor.add_vendor_path(str(tmp_path))
    assert chosen == str(tmp_path / "_vendor")
