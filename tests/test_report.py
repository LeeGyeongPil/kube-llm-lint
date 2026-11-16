"""AC-8: JSON shape, line numbers and secret protection."""

from __future__ import annotations

import json

from kube_llm_lint.cli import main

from helpers import ROOT

BROKEN = str(ROOT / "examples" / "broken.yaml")
FIELDS = {"file", "line", "kind", "name", "namespace", "container", "server",
          "rule", "severity", "message", "fix"}
SECRET = "hf_exampleexampleexample"


def output(capsys, *argv):
    main(list(argv), environ={})
    return capsys.readouterr().out


def test_json_schema_fields(capsys):
    data = json.loads(output(capsys, BROKEN, "--format", "json", "--lang", "en"))
    assert set(data) == {"findings", "summary"}
    assert len(data["findings"]) == 8
    for finding in data["findings"]:
        assert set(finding) == FIELDS
        assert finding["severity"] in {"error", "warning", "info"}
        assert isinstance(finding["line"], int) and finding["line"] > 0
        assert finding["message"]
    assert data["summary"] == {"errors": 6, "warnings": 2, "info": 0, "containers": 3, "files": 1}


def test_line_numbers(capsys):
    data = json.loads(output(capsys, BROKEN, "--format", "json", "--lang", "en"))
    lines = {(f["name"], f["rule"]): f["line"] for f in data["findings"]}
    assert lines[("llama-70b", "gpu-parallel-mismatch")] == 20  # "- name: vllm"
    assert lines[("llama-70b", "hf-token-plaintext")] == 28  # the HF_TOKEN env entry
    assert lines[("sglang", "shm-missing")] == 50
    assert lines[("tgi", "shm-missing")] == 80
    text = open(BROKEN).read().split("\n")
    assert "name: vllm" in text[19] and "HF_TOKEN" in text[27]


def test_text_output_location_and_summary(capsys):
    out = output(capsys, BROKEN, "--lang", "en")
    assert f"{BROKEN}:20: error" in out
    assert out.strip().splitlines()[-1].startswith("Summary: errors 6, warnings 2")


def test_token_never_printed(capsys):
    assert SECRET in open(BROKEN).read()
    for argv in ([BROKEN], [BROKEN, "--format", "json"], [BROKEN, "--lang", "ko"]):
        out = output(capsys, *argv)
        assert SECRET not in out
        assert "hf_****" in out
    for part in ("hf_example", "exampleexample"):
        assert part not in output(capsys, BROKEN, "--format", "json")


def test_fix_snippets_are_valid_yaml(capsys):
    from ruamel.yaml import YAML

    data = json.loads(output(capsys, BROKEN, "--format", "json"))
    for finding in data["findings"]:
        if finding["rule"] == "mem-fraction-range":
            continue  # an argument, not a YAML block
        assert YAML(typ="safe").load(finding["fix"]) is not None
