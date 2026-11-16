"""SARIF 2.1.0 output (--format sarif)."""

from __future__ import annotations

import json

import pytest

from kube_llm_lint import rules
from kube_llm_lint.cli import main

from helpers import ROOT

BROKEN = str(ROOT / "examples" / "broken.yaml")
LEVELS = {"error": "error", "warning": "warning", "info": "note"}


def run(capsys, *argv):
    code = main(list(argv), environ={})
    return code, capsys.readouterr().out


def test_structure_and_rule_ids(capsys):
    code, out = run(capsys, BROKEN, "--format", "sarif")
    log = json.loads(out)
    assert code == 1
    assert log["version"] == "2.1.0"
    assert "$schema" in log
    driver = log["runs"][0]["tool"]["driver"]
    assert driver["name"] == "kube-llm-lint"
    assert {r["id"] for r in driver["rules"]} == set(rules.BY_ID)
    assert [r["id"] for r in driver["rules"]] == [r.id for r in rules.RULES]


def test_results_match_json(capsys):
    _, sarif_out = run(capsys, BROKEN, "--format", "sarif")
    _, json_out = run(capsys, BROKEN, "--format", "json")
    results = json.loads(sarif_out)["runs"][0]["results"]
    findings = json.loads(json_out)["findings"]
    assert len(results) == len(findings) > 0
    for res, f in zip(results, findings):
        loc = res["locations"][0]["physicalLocation"]
        assert res["ruleId"] == f["rule"]
        assert loc["region"]["startLine"] == max(f["line"], 1)
        assert loc["artifactLocation"]["uri"] == f["file"]
        assert res["message"]["text"].startswith(f["message"])


def test_level_mapping(capsys):
    _, out = run(capsys, BROKEN, "--format", "sarif")
    run_ = json.loads(out)["runs"][0]
    driver_rules = run_["tool"]["driver"]["rules"]
    by_rule = {r["id"]: i for i, r in enumerate(driver_rules)}
    for res in run_["results"]:
        assert res["ruleIndex"] == by_rule[res["ruleId"]]
        assert res["level"] == LEVELS[rules.BY_ID[res["ruleId"]].severity]
    # all three severities map in the rule table (error, warning, info -> note)
    assert {r["defaultConfiguration"]["level"] for r in driver_rules} == {"error", "warning", "note"}
    assert {"error", "warning"} <= {res["level"] for res in run_["results"]}


@pytest.mark.parametrize("extra", [[], ["--fail-on", "none"], ["--fail-on", "warning"]])
def test_exit_code_same_as_json(capsys, extra):
    code_sarif, _ = run(capsys, BROKEN, "--format", "sarif", *extra)
    code_json, _ = run(capsys, BROKEN, "--format", "json", *extra)
    assert code_sarif == code_json


def test_empty_input_has_no_results(capsys, tmp_path):
    f = tmp_path / "cm.yaml"
    f.write_text("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\n")
    code, out = run(capsys, str(f), "--format", "sarif")
    assert code == 0
    assert json.loads(out)["runs"][0]["results"] == []


def test_rule_text_follows_language(capsys):
    _, ko = run(capsys, BROKEN, "--format", "sarif", "--lang", "ko")
    _, en = run(capsys, BROKEN, "--format", "sarif", "--lang", "en")
    ko_rules = json.loads(ko)["runs"][0]["tool"]["driver"]["rules"]
    en_rules = json.loads(en)["runs"][0]["tool"]["driver"]["rules"]
    assert ko_rules != en_rules


def test_stdin_uri_is_uri_safe(capsys, monkeypatch):
    import io
    import sys

    monkeypatch.setattr(sys, "stdin", io.StringIO(open(BROKEN, encoding="utf-8").read()))
    code, out = run(capsys, "-", "--format", "sarif")
    uris = {
        r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        for r in json.loads(out)["runs"][0]["results"]
    }
    assert code == 1
    assert uris == {"stdin"}
