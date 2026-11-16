"""AC-7 and AC-9: inputs, exit codes and run-time controls."""

from __future__ import annotations

import io
import json
import subprocess
import sys

import pytest

from kube_llm_lint.cli import main

from helpers import FIXTURES, ROOT

BROKEN = str(ROOT / "examples" / "broken.yaml")
KUSTOMIZED = FIXTURES / "kustomize" / "broken-kustomized.yaml"
DOCS = str(FIXTURES / "upstream" / "vllm-docs-nvidia.yaml")


def run(capsys, *argv, env=None):
    code = main(list(argv), environ=env or {})
    out = capsys.readouterr()
    return code, out.out, out.err


def test_stdin_equals_file(capsys, monkeypatch):
    code_file, out_file, _ = run(capsys, str(KUSTOMIZED), "--format", "json")
    monkeypatch.setattr(sys, "stdin", io.StringIO(KUSTOMIZED.read_text()))
    code_stdin, out_stdin, _ = run(capsys, "-", "--format", "json")
    from_file, from_stdin = json.loads(out_file), json.loads(out_stdin)
    assert code_file == code_stdin == 1
    assert from_stdin["summary"]["errors"] + from_stdin["summary"]["warnings"] == 8
    strip = lambda d: [{k: v for k, v in f.items() if k != "file"} for f in d["findings"]]  # noqa: E731
    assert strip(from_file) == strip(from_stdin)
    assert {f["file"] for f in from_stdin["findings"]} == {"<stdin>"}


def test_stdin_through_real_process():
    proc = subprocess.run(
        [sys.executable, "-m", "kube_llm_lint", "-", "--format", "json", "--lang", "en"],
        input=KUSTOMIZED.read_text(), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 1
    assert len(json.loads(proc.stdout)["findings"]) == 8


def test_directory_and_multiple_files_match(capsys, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.yaml").write_text(open(BROKEN).read())
    (tmp_path / "b.yml").write_text(open(DOCS).read())
    (tmp_path / "ignore.txt").write_text("not yaml {{")
    _, via_dir, _ = run(capsys, str(tmp_path), "--format", "json")
    _, via_files, _ = run(capsys, str(tmp_path / "b.yml"), str(tmp_path / "sub" / "a.yaml"), "--format", "json")
    assert json.loads(via_dir)["findings"] == json.loads(via_files)["findings"]
    assert json.loads(via_dir)["summary"]["files"] == 2


def test_template_input_exit2(capsys):
    code, out, err = run(capsys, str(FIXTURES / "errors" / "helm-template.yaml"), env={})
    assert code == 2
    assert out == ""
    assert "helm template" in err


def test_missing_file_exit2(capsys):
    code, _, err = run(capsys, "does-not-exist.yaml")
    assert code == 2
    assert "does-not-exist.yaml" in err


def test_broken_yaml_exit2(capsys):
    code, _, err = run(capsys, str(FIXTURES / "errors" / "broken-yaml.yaml"))
    assert code == 2
    assert "YAML" in err


def test_no_input_and_bad_options_exit2(capsys):
    assert run(capsys)[0] == 2
    assert run(capsys, BROKEN, "--startup-budget", "0")[0] == 2
    code, _, err = run(capsys, BROKEN, "--disable", "not-a-rule")
    assert code == 2 and "not-a-rule" in err and "shm-missing" in err
    with pytest.raises(SystemExit) as exc:
        main([BROKEN, "--fail-on", "loud"], environ={})
    assert exc.value.code == 2


def test_disable_and_annotation(capsys, tmp_path):
    _, out, _ = run(capsys, BROKEN, "--format", "json", "--disable", "shm-missing,hf-token-plaintext")
    rules = {f["rule"] for f in json.loads(out)["findings"]}
    assert not rules & {"shm-missing", "hf-token-plaintext"}
    assert "gpu-parallel-mismatch" in rules

    text = open(DOCS).read().replace(
        "  name: mistral-7b\n  namespace: default\n  labels:",
        "  name: mistral-7b\n  namespace: default\n  annotations:\n    kube-llm-lint/ignore: no-startup-probe\n  labels:",
    )
    assert "kube-llm-lint/ignore" in text
    path = tmp_path / "ignored.yaml"
    path.write_text(text)
    _, out, _ = run(capsys, str(path), "--format", "json")
    assert json.loads(out)["findings"] == []


def test_pod_template_annotation_also_ignores(capsys, tmp_path):
    text = open(DOCS).read().replace(
        "    metadata:\n      labels:\n        app: mistral-7b\n",
        "    metadata:\n      annotations:\n        kube-llm-lint/ignore: no-startup-probe\n      labels:\n        app: mistral-7b\n",
    )
    assert "kube-llm-lint/ignore" in text
    path = tmp_path / "t.yaml"
    path.write_text(text)
    assert json.loads(run(capsys, str(path), "--format", "json")[1])["findings"] == []


def test_fail_on_levels(capsys):
    # the official example has one warning and nothing worse
    assert run(capsys, DOCS)[0] == 0
    assert run(capsys, DOCS, "--fail-on", "error")[0] == 0
    assert run(capsys, DOCS, "--fail-on", "warning")[0] == 1
    assert run(capsys, DOCS, "--fail-on", "info")[0] == 1
    assert run(capsys, DOCS, "--fail-on", "none")[0] == 0
    assert run(capsys, BROKEN, "--fail-on", "none")[0] == 0
    assert run(capsys, BROKEN)[0] == 1


def test_startup_budget_flag_changes_result(capsys):
    assert run(capsys, DOCS, "--fail-on", "warning", "--startup-budget", "90")[0] == 0
    assert run(capsys, DOCS, "--fail-on", "warning", "--startup-budget", "91")[0] == 1


def test_list_rules_has_sources(capsys):
    from kube_llm_lint.rules import RULES

    code, out, _ = run(capsys, "--list-rules", "--lang", "en")
    assert code == 0
    blocks = [b for b in out.strip().split("\n\n") if b.strip()]
    assert len(blocks) == len(RULES)
    for rule, block in zip(RULES, blocks):
        assert block.startswith(rule.id)
        assert rule.severity in block
        assert "https://" in block
        assert "2026-" in block
