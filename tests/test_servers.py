"""AC-4: argument forms are normalised and the required GPU count is right."""

from __future__ import annotations

import textwrap

import pytest

from kube_llm_lint.loader import parse_text
from kube_llm_lint.servers import inspect_container

from helpers import lint_text, pod, rules_of


def container(yaml_text: str):
    doc = parse_text("c:\n  " + textwrap.indent(textwrap.dedent(yaml_text).strip("\n"), "  ").lstrip(), "x")[0]
    return doc.data["c"]


FORMS = {
    "sh-c-string": ('''
        image: vllm/vllm-openai:v0.11.0
        command: ["/bin/sh", "-c"]
        args: ["vllm serve m --tensor-parallel-size 4"]''', "vllm", 4),
    "command-list": ('''
        image: x
        command: ["vllm", "serve", "m", "--tensor-parallel-size", "2"]''', "vllm", 2),
    "equals-form": ('''
        image: vllm/vllm-openai:v0.11.0
        args: ["--model=m", "--tensor-parallel-size=8"]''', "vllm", 8),
    "short-aliases": ('''
        image: x
        command: ["vllm", "serve", "m", "-tp", "2", "-pp", "2"]''', "vllm", 4),
    "data-parallel": ('''
        image: x
        command: ["vllm", "serve", "m", "-tp", "2", "-dp", "2"]''', "vllm", 4),
    "dp-local-replaces-dp": ('''
        image: x
        command: ["vllm", "serve", "m", "-tp", "2", "--data-parallel-size", "4", "--data-parallel-size-local", "2"]''', "vllm", 4),
    "python-m-vllm": ('''
        image: x
        command: ["python3", "-m", "vllm.entrypoints.openai.api_server"]
        args: ["--model", "m", "--tensor-parallel-size", "2"]''', "vllm", 2),
    "image-only-entrypoint": ('''
        image: vllm/vllm-openai:v0.11.0
        args: ["--model", "m", "--tensor-parallel-size", "2"]''', "vllm", 2),
    "sglang-tp-size": ('''
        image: x
        command: ["python3", "-m", "sglang.launch_server", "--model-path", "m", "--tp-size", "4"]''', "sglang", 4),
    "sglang-tp-dp": ('''
        image: x
        command: ["python3", "-m", "sglang.launch_server", "--tp", "2", "--dp", "2"]''', "sglang", 4),
    "sglang-pp": ('''
        image: x
        command: ["python3", "-m", "sglang.launch_server", "--tp=2", "--pp-size", "2"]''', "sglang", 4),
    "tgi-num-shard-arg": ('''
        image: ghcr.io/huggingface/text-generation-inference:3.3.5
        args: ["--model-id", "m", "--num-shard", "2"]''', "tgi", 2),
    "tgi-env": ('''
        image: ghcr.io/huggingface/text-generation-inference:3.3.5
        env:
        - {name: NUM_SHARD, value: "4"}''', "tgi", 4),
    "k8s-var": ('''
        image: vllm/vllm-openai:v0.11.0
        env:
        - {name: TP, value: "4"}
        args: ["--tensor-parallel-size", "$(TP)"]''', "vllm", 4),
    "shell-var": ('''
        image: x
        env:
        - {name: TP, value: "4"}
        command: ["bash", "-c"]
        args: ["vllm serve m --tensor-parallel-size ${TP}"]''', "vllm", 4),
    "shell-default": ('''
        image: x
        command: ["sh", "-c", "vllm serve m --tensor-parallel-size ${TP:-2}"]''', "vllm", 2),
    "chained-shell": ('''
        image: x
        command: ["sh", "-c", "pip install foo && exec vllm serve m -tp 2 \\n --max-model-len 4096"]''', "vllm", 2),
}


@pytest.mark.parametrize("name", list(FORMS))
def test_required_gpus_by_form(name):
    yaml_text, server, expected = FORMS[name]
    spec = inspect_container(container(yaml_text))
    assert spec is not None
    assert spec.server == server
    assert spec.required_gpus == expected


def test_lws_uses_tensor_parallel_only():
    c = container('''
        image: x
        command: ["vllm", "serve", "m", "-tp", "4", "-pp", "2"]''')
    assert inspect_container(c, lws=True).required_gpus == 4
    assert inspect_container(c, lws=False).required_gpus == 8


def test_non_serving_container_is_ignored():
    assert inspect_container(container('''
        image: nginx:1.27
        args: ["--tp", "4"]''')) is None


def test_unsupported_vllm_env_ignored():
    # VLLM_TENSOR_PARALLEL_SIZE is not a documented vLLM setting, so it must not count.
    spec = inspect_container(container('''
        image: vllm/vllm-openai:v0.11.0
        env:
        - {name: VLLM_TENSOR_PARALLEL_SIZE, value: "4"}
        args: ["--model", "m"]'''))
    assert spec.tp == 1
    assert spec.required_gpus == 1


def test_config_file_and_unresolved_are_info():
    text = pod('''
        name: v
        image: vllm/vllm-openai:v0.11.0
        args: ["--config", "/etc/vllm.yaml", "--tensor-parallel-size", "$(UNKNOWN_TP)"]
        resources: {limits: {nvidia.com/gpu: 2}}''')
    findings = lint_text(text).findings
    by_rule = {f.rule: f for f in findings}
    assert by_rule["config-file-unchecked"].severity == "info"
    assert by_rule["arg-unresolved"].severity == "info"
    # the unresolved value must not trigger a GPU/shm verdict
    assert "gpu-parallel-mismatch" not in by_rule
    assert "shm-missing" not in by_rule
    assert rules_of(findings).count("arg-unresolved") == 1
