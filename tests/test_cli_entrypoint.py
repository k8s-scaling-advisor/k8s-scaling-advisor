"""Tests for package CLI entrypoint wrapper."""

import sys
import types

import k8s_advisor.cli as cli


def test_cli_main_delegates_to_root_main(monkeypatch):
    called = {"value": False}

    def fake_root_main():
        called["value"] = True

    fake_module = types.SimpleNamespace(main=fake_root_main)
    monkeypatch.setitem(sys.modules, "main", fake_module)
    cli.main()
    assert called["value"] is True
