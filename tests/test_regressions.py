"""Regression tests for review round 1 (false GPU mismatches, localized errors)."""

from __future__ import annotations

import json
import re
import textwrap

import pytest

from kube_llm_lint.cli import main
from kube_llm_lint.loader import parse_text
from kube_llm_lint.servers import inspect_container

from helpers import ROOT, lint_text, pod, rules_of

BROKEN = str(ROOT / "examples" / "broken.yaml")
HANGUL = re.compile("[가-힣]")


def container(yaml_text: str):
    body = textwrap.indent(textwrap.dedent(yaml_text).strip("\n"), "  ")
    return parse_text("c:\n" + body + "\n", "x")[0].data["c"]


def run(capsys, *argv):
    try:
        code = main(list(argv), environ={})
    except SystemExit as exc:
        code = int(exc.code or 0)
    return code, capsys.readouterr()


# ---- Q-1: containers whose command we cannot read are not judged

RAY_WORKER = """
    image: vllm/vllm-openai
    command: ["sh", "-c", "bash /vllm-workspace/examples/online_serving/multi-node-serving.sh worker --ray_address=$(LWS_LEADER_ADDRESS)"]
    resources: {limits: {nvidia.com/gpu: 8}}"""


def test_ray_worker_is_not_judged():
    assert inspect_container(container(RAY_WORKER)) is None
    text = pod(RAY_WORKER)
    assert "gpu-parallel-mismatch" not in rules_of(lint_text(text).findings)


def test_custom_start_script_is_not_judged():
    text = pod("""
        name: v
        image: vllm/vllm-openai
        command: ["/bin/bash", "/scripts/start.sh"]
        resources: {limits: {nvidia.com/gpu: 4}}""")
    assert lint_text(text).findings == []
    assert lint_text(text).containers == 0


def test_image_entrypoint_with_args_is_still_judged():
    text = pod("""
        name: v
        image: vllm/vllm-openai:v0.11.0
        args: ["--model", "/m", "--tensor-parallel-size", "4"]
        resources: {limits: {nvidia.com/gpu: 2}}""")
    assert "gpu-parallel-mismatch" in rules_of(lint_text(text).findings)


# ---- Q-2: vLLM accepts underscores in long flag names

@pytest.mark.parametrize("args, expected", [
    ('["--model", "/m", "--tensor_parallel_size", "4"]', 4),
    ('["--tensor_parallel_size=2", "--pipeline_parallel_size", "2"]', 4),
])
def test_underscore_flags_are_read(args, expected):
    spec = inspect_container(container(f"""
        image: vllm/vllm-openai:v0.11.0
        args: {args}"""))
    assert spec is not None and spec.required_gpus == expected


def test_underscore_flags_no_false_mismatch():
    text = pod("""
        name: v
        image: vllm/vllm-openai:v0.11.0
        args: ["--model", "/models/x", "--tensor_parallel_size", "4"]
        resources: {limits: {nvidia.com/gpu: 4}}""")
    assert "gpu-parallel-mismatch" not in rules_of(lint_text(text).findings)


def test_underscore_not_applied_to_sglang():
    spec = inspect_container(container("""
        image: x
        command: ["python3", "-m", "sglang.launch_server", "--tensor_parallel_size", "4"]"""))
    assert spec is not None and spec.required_gpus == 1


# ---- Q-3: localized argparse and encoding errors

@pytest.mark.parametrize("argv", [
    [BROKEN, "--format", "xml"],
    [BROKEN, "--startup-budget", "abc"],
    [BROKEN, "--bogus"],
    [BROKEN, "--format"],
])
def test_argparse_errors_localized(capsys, argv):
    code, ko = run(capsys, *argv, "--lang", "ko")
    assert code == 2
    last = ko.err.strip().splitlines()[-1]
    assert HANGUL.search(last)
    assert not re.search(r"invalid|choose from|expected one|unrecognized", last)
    code, en = run(capsys, *argv, "--lang", "en")
    assert code == 2
    assert not HANGUL.search(en.err.strip().splitlines()[-1])


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_non_utf8_file_has_fix_hint(tmp_path, capsys, lang):
    bad = tmp_path / "bad.yaml"
    bad.write_bytes(b"\xff\xfe\x00bad")
    code, out = run(capsys, str(bad), "--lang", lang)
    assert code == 2
    assert "codec" not in out.err and "UTF-8" in out.err
    assert bool(HANGUL.search(out.err)) == (lang == "ko")


# ---- Q-5: counts and empty inputs

def test_empty_directory_is_an_error(tmp_path, capsys):
    code, out = run(capsys, str(tmp_path), "--lang", "en")
    assert code == 2 and "no *.yaml" in out.err


def test_empty_file_is_counted(tmp_path, capsys):
    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    code, out = run(capsys, str(empty), "--format", "json")
    assert code == 0 and json.loads(out.out)["summary"]["files"] == 1


def test_summary_has_no_plural_forms(capsys):
    code, out = run(capsys, BROKEN, "--lang", "en")
    assert "1 GPUs" not in out.out
    assert not re.search(r"\b1 (errors|warnings|serving containers|files)\b", out.out)
