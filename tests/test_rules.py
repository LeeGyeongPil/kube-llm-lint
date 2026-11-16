"""AC-3: the three-server fixture gives exactly the expected findings."""

from __future__ import annotations

from collections import Counter

from kube_llm_lint.checks import lint

from helpers import FIXTURES, ROOT

EXPECTED = Counter({
    ("gpu-parallel-mismatch", "Deployment/llama-70b"): 1,
    ("hf-cache-ephemeral", "Deployment/llama-70b"): 1,
    ("hf-token-plaintext", "Deployment/llama-70b"): 1,
    ("mem-fraction-range", "Deployment/llama-70b"): 1,
    ("no-startup-probe", "Deployment/llama-70b"): 1,
    ("shm-missing", "Deployment/llama-70b"): 1,
    ("shm-missing", "Pod/sglang"): 1,
    ("shm-missing", "Deployment/tgi"): 1,
})


def summarize(path):
    return Counter((f.rule, f"{f.kind}/{f.name}") for f in lint([str(path)]))


def test_broken_three_servers_exact():
    assert summarize(ROOT / "examples" / "broken.yaml") == EXPECTED


def test_kustomized_same_findings():
    # keys re-ordered and a namespace added, as `kubectl kustomize` does
    assert summarize(FIXTURES / "kustomize" / "broken-kustomized.yaml") == EXPECTED


def test_good_has_no_findings():
    assert lint([str(ROOT / "examples" / "good.yaml")]) == []
