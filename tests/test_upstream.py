"""AC-2: real upstream manifests (see NOTICE)."""

from __future__ import annotations

from kube_llm_lint.checks import lint

from helpers import FIXTURES


def test_vllm_docs_nvidia_only_startup_probe():
    findings = lint([str(FIXTURES / "upstream" / "vllm-docs-nvidia.yaml")])
    # liveness 60 + 10 x 3 = 90s, no startupProbe
    assert [(f.rule, f.data["budget"]) for f in findings] == [("no-startup-probe", 90)]


def test_vllm_chart_rendered_startup_probe():
    findings = lint([str(FIXTURES / "upstream" / "vllm-chart-helm-rendered.yaml")])
    probe = [f for f in findings if f.rule == "no-startup-probe"]
    assert [f.data["budget"] for f in probe] == [45]  # 15 + 10 x 3
    assert not [f for f in findings if f.rule == "gpu-parallel-mismatch"]
    # the model path /data/ is local, so the cache rule must stay quiet
    assert not [f for f in findings if f.rule == "hf-cache-ephemeral"]
