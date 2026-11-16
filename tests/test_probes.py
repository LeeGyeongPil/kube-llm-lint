"""AC-6: probe budget maths and the suggested startupProbe."""

from __future__ import annotations

from ruamel.yaml import YAML

from kube_llm_lint.checks import probe_budget

from helpers import lint_text, pod

BASE = '''
    name: v
    image: vllm/vllm-openai:v0.11.0
    args: ["--model", "/m"]
    resources: {limits: {nvidia.com/gpu: 1}}
'''
HTTP = "{httpGet: {path: /health, port: 8000}"


def probes(extra):
    return lint_text(pod(BASE + extra)).findings


def test_budget_defaults():
    # Kubernetes defaults: initialDelay 0, period 10, failureThreshold 3
    assert probe_budget({"httpGet": {"path": "/health", "port": 8000}}) == 30
    assert probe_budget({"initialDelaySeconds": 60, "periodSeconds": 10}) == 90
    assert probe_budget({"initialDelaySeconds": 15, "periodSeconds": 10, "failureThreshold": 60}) == 615
    assert probe_budget(None) is None
    found = probes(f"livenessProbe: {HTTP}}}\n")
    assert [(f.rule, f.data["budget"]) for f in found] == [("no-startup-probe", 30)]


def test_no_probe_no_finding():
    assert probes("") == []


def test_startup_probe_long_enough_is_clean():
    extra = (f"livenessProbe: {HTTP}}}\n"
             f"startupProbe: {HTTP}, periodSeconds: 10, failureThreshold: 60}}\n")
    assert probes(extra) == []


def test_startup_probe_too_short():
    found = probes(f"startupProbe: {HTTP}, periodSeconds: 10, failureThreshold: 6}}\n")
    assert [(f.rule, f.data["budget"]) for f in found] == [("probe-budget-short", 60)]


def test_startup_budget_option():
    extra = f"livenessProbe: {HTTP}, periodSeconds: 10, failureThreshold: 12}}\n"
    assert [f.rule for f in lint_text(pod(BASE + extra), startup_budget=600).findings] == ["no-startup-probe"]
    # a 120s liveness budget is enough when the target is only 60s
    assert lint_text(pod(BASE + extra), startup_budget=60).findings == []


def test_fix_snippet_threshold():
    extra = f"livenessProbe: {HTTP}, periodSeconds: 10}}\n"
    finding = probes(extra)[0]
    snippet = YAML(typ="safe").load(finding.fix)["startupProbe"]
    assert snippet["failureThreshold"] == 60  # ceil((600 - 0) / 10)
    assert snippet["httpGet"]["path"] == "/health"
    assert snippet["httpGet"]["port"] == 8000
    custom = lint_text(pod(BASE + extra), startup_budget=95).findings[0]
    assert YAML(typ="safe").load(custom.fix)["startupProbe"]["failureThreshold"] == 10  # ceil(95/10)


def test_fix_snippet_keeps_exec_handler():
    extra = "livenessProbe: {exec: {command: [cat, /tmp/ok]}}\n"
    snippet = YAML(typ="safe").load(probes(extra)[0].fix)["startupProbe"]
    assert snippet["exec"] == {"command": ["cat", "/tmp/ok"]}


import pytest

PROBE_KEYS = ["initialDelaySeconds", "periodSeconds", "failureThreshold"]


def _probe_extra(kind: str, key: str, value: str) -> str:
    probe = f"{kind}: {{httpGet: {{path: /health, port: 8000}}, {key}: {value}}}\n"
    return probe


@pytest.mark.parametrize("key", PROBE_KEYS)
@pytest.mark.parametrize("value", ["null", "~", ""])
def test_null_values_use_defaults_liveness(key, value):
    found = probes(_probe_extra("livenessProbe", key, value))
    assert [(f.rule, f.data["budget"]) for f in found] == [("no-startup-probe", 30)]


@pytest.mark.parametrize("key", PROBE_KEYS)
@pytest.mark.parametrize("value", ["null", "~", ""])
def test_null_values_use_defaults_startup(key, value):
    found = probes(_probe_extra("startupProbe", key, value))
    assert [(f.rule, f.data["budget"]) for f in found] == [("probe-budget-short", 30)]


@pytest.mark.parametrize("kind,rule_arg", [("livenessProbe", "livenessProbe"),
                                           ("startupProbe", "startupProbe")])
@pytest.mark.parametrize("key", PROBE_KEYS)
def test_non_numeric_value_reports_arg_unresolved(kind, rule_arg, key):
    found = probes(_probe_extra(kind, key, "abc"))
    assert [f.rule for f in found] == ["arg-unresolved"]
    assert f"{rule_arg}.{key}" in found[0].message


def test_zero_delay_unchanged():
    found = probes(_probe_extra("livenessProbe", "initialDelaySeconds", "0"))
    assert [(f.rule, f.data["budget"]) for f in found] == [("no-startup-probe", 30)]
