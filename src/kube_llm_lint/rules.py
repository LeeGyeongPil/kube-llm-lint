"""Rule table: id, severity, source URL and the date the source was checked."""

from __future__ import annotations

from dataclasses import dataclass

ERROR, WARNING, INFO = "error", "warning", "info"
SEVERITY_RANK = {INFO: 1, WARNING: 2, ERROR: 3}

VLLM_K8S = "https://docs.vllm.ai/en/latest/deployment/k8s/"
VLLM_ARGS = "https://docs.vllm.ai/en/latest/configuration/engine_args/"
VLLM_ISSUE_PROBE = "https://github.com/vllm-project/vllm/issues/17386"
TGI_SHM = "https://github.com/huggingface/text-generation-inference#a-note-on-shared-memory-shm"
SGLANG = "https://github.com/sgl-project/sglang"
K8S_PROBES = (
    "https://kubernetes.io/docs/tasks/configure-pod-container/"
    "configure-liveness-readiness-startup-probes/"
)
CHECKED = "2026-11-16"


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    sources: tuple[str, ...]
    checked: str = CHECKED

    @property
    def source(self) -> str:
        return self.sources[0]


RULES: tuple[Rule, ...] = (
    Rule("gpu-parallel-mismatch", ERROR, (VLLM_ARGS, SGLANG)),
    Rule("gpu-missing", WARNING, (VLLM_K8S,)),
    Rule("gpu-mig-unchecked", INFO, (VLLM_K8S,)),
    Rule("shm-missing", ERROR, (TGI_SHM, VLLM_K8S)),
    Rule("shm-not-memory", WARNING, (TGI_SHM,)),
    Rule("shm-too-small", WARNING, (TGI_SHM,)),
    Rule("no-startup-probe", WARNING, (VLLM_ISSUE_PROBE, K8S_PROBES)),
    Rule("probe-budget-short", WARNING, (VLLM_ISSUE_PROBE, K8S_PROBES)),
    Rule("hf-cache-ephemeral", WARNING, (VLLM_K8S,)),
    Rule("hf-token-plaintext", ERROR, (VLLM_K8S,)),
    Rule("mem-fraction-range", ERROR, (VLLM_ARGS, SGLANG)),
    Rule("config-file-unchecked", INFO, (VLLM_ARGS,)),
    Rule("arg-unresolved", INFO, (VLLM_ARGS,)),
)
BY_ID = {rule.id: rule for rule in RULES}
