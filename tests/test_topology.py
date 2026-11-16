"""AC-5: LWS, DP, MIG, shared-memory and cache variants."""

from __future__ import annotations

import pytest

from helpers import lint_text, pod, rules_of

SHM_VOLUME = "volumes:\n- name: shm\n  emptyDir: {medium: Memory, sizeLimit: 2Gi}\n"
MOUNT_SHM = "volumeMounts:\n- {name: shm, mountPath: /dev/shm}\n"
PVC = "volumes:\n- name: c\n  persistentVolumeClaim: {claimName: x}\n"


def run_vllm(args, gpus, extra="", pod_extra=""):
    text = f"""
        name: v
        image: vllm/vllm-openai:v0.11.0
        args: {args}
        resources: {{limits: {{nvidia.com/gpu: {gpus}}}}}
    """
    return lint_text(pod(text + extra, pod_extra)).findings


def test_matching_gpus_with_shm_is_clean():
    assert run_vllm('["--model", "/m", "-tp", "2"]', 2, MOUNT_SHM, SHM_VOLUME) == []


def test_amd_gpu_resource_counts():
    text = pod("""
        name: v
        image: vllm/vllm-openai:v0.11.0
        args: ["--model", "/m", "-tp", "2"]
        resources: {requests: {amd.com/gpu: "1"}}
    """ + MOUNT_SHM, SHM_VOLUME)
    found = [x for x in lint_text(text).findings if x.rule == "gpu-parallel-mismatch"]
    assert len(found) == 1 and found[0].data == {"required": 2, "gpus": 1}


def test_limits_win_over_requests():
    text = pod("""
        name: v
        image: vllm/vllm-openai:v0.11.0
        args: ["--model", "/m", "-tp", "2"]
        resources: {requests: {nvidia.com/gpu: 1}, limits: {nvidia.com/gpu: 2}}
    """ + MOUNT_SHM, SHM_VOLUME)
    assert lint_text(text).findings == []


def test_dp_is_multiplied():
    two_by_two = '["--model", "/m", "-tp", "2", "-dp", "2"]'
    assert "gpu-parallel-mismatch" in rules_of(run_vllm(two_by_two, 2, MOUNT_SHM, SHM_VOLUME))
    assert run_vllm(two_by_two, 4, MOUNT_SHM, SHM_VOLUME) == []


def test_dp_local_replaces_dp():
    args = '["--model", "/m", "-tp", "2", "--data-parallel-size", "4", "--data-parallel-size-local", "2"]'
    assert run_vllm(args, 4, MOUNT_SHM, SHM_VOLUME) == []


LWS = """
    apiVersion: leaderworkerset.x-k8s.io/v1
    kind: LeaderWorkerSet
    metadata: {name: big}
    spec:
      leaderWorkerTemplate:
        size: 2
        workerTemplate:
          spec:
            volumes:
            - name: shm
              emptyDir: {medium: Memory, sizeLimit: 2Gi}
            containers:
            - name: vllm
              image: vllm/vllm-openai:v0.11.0
              args: ["--model", "/m", "--tensor-parallel-size", "8", "--pipeline-parallel-size", "2"]
              resources: {limits: {nvidia.com/gpu: 8}}
              volumeMounts: [{name: shm, mountPath: /dev/shm}]
"""


def test_leader_worker_set_compares_tp_only():
    assert lint_text(LWS).findings == []
    found = lint_text(LWS.replace("nvidia.com/gpu: 8", "nvidia.com/gpu: 4")).findings
    assert [(f.rule, f.data["required"]) for f in found] == [("gpu-parallel-mismatch", 8)]


def test_mig_is_info_and_not_compared():
    text = pod("""
        name: v
        image: vllm/vllm-openai:v0.11.0
        args: ["--model", "/m", "-tp", "2"]
        resources: {limits: {nvidia.com/mig-1g.10gb: 1}}
    """ + MOUNT_SHM, SHM_VOLUME)
    found = lint_text(text).findings
    assert [(f.rule, f.severity) for f in found] == [("gpu-mig-unchecked", "info")]


def test_host_ipc_skips_shm():
    assert run_vllm('["--model", "/m", "-tp", "2"]', 2, pod_extra="hostIPC: true") == []


def test_nccl_shm_disable_skips_shm():
    text = pod("""
        name: v
        image: vllm/vllm-openai:v0.11.0
        args: ["--model", "/m", "-tp", "2"]
        env: [{name: NCCL_SHM_DISABLE, value: "1"}]
        resources: {limits: {nvidia.com/gpu: 2}}
    """)
    assert lint_text(text).findings == []


def test_single_gpu_needs_no_shm():
    assert run_vllm('["--model", "/m"]', 1) == []


def shm_case(volume_yaml):
    text = pod("""
        name: v
        image: vllm/vllm-openai:v0.11.0
        args: ["--model", "/m", "-tp", "2"]
        resources: {limits: {nvidia.com/gpu: 2}}
    """ + MOUNT_SHM, volume_yaml)
    return rules_of(lint_text(text).findings)


def test_shm_not_memory():
    assert shm_case("volumes:\n- name: shm\n  emptyDir: {}\n") == ["shm-not-memory"]


def test_shm_too_small():
    assert shm_case("volumes:\n- name: shm\n  emptyDir: {medium: Memory, sizeLimit: 512Mi}\n") == ["shm-too-small"]


@pytest.mark.parametrize("size,small", [
    ("2Gi", False), ("1Gi", False), ("1536Mi", False), ("1G", True), ("512Mi", True),
])
def test_shm_size_threshold(size, small):
    result = shm_case(f"volumes:\n- name: shm\n  emptyDir: {{medium: Memory, sizeLimit: {size}}}\n")
    assert (result == ["shm-too-small"]) is small


def test_gpu_missing_and_cpu_image_exemption():
    text = pod("""
        name: v
        image: vllm/vllm-openai:v0.11.0
        args: ["--model", "/m"]
    """)
    assert rules_of(lint_text(text).findings) == ["gpu-missing"]
    assert lint_text(text.replace("vllm-openai:v0.11.0", "vllm-cpu:v0.11.0")).findings == []


def test_tgi_without_num_shard_uses_gpu_count_for_shm():
    text = pod("""
        name: t
        image: ghcr.io/huggingface/text-generation-inference:3.3.5
        env: [{name: MODEL_ID, value: /data/m}]
        resources: {limits: {nvidia.com/gpu: 2}}
    """)
    assert rules_of(lint_text(text).findings) == ["shm-missing"]


CACHE_BASE = """
    name: v
    image: vllm/vllm-openai:v0.11.0
    args: ["--model", "org/model"]
    resources: {limits: {nvidia.com/gpu: 1}}
"""


def test_hf_cache_without_mount():
    assert rules_of(lint_text(pod(CACHE_BASE)).findings) == ["hf-cache-ephemeral"]


def test_hf_cache_on_pvc_is_clean():
    mount = "volumeMounts: [{name: c, mountPath: /root/.cache/huggingface}]\n"
    assert lint_text(pod(CACHE_BASE + mount, PVC)).findings == []


def test_hf_cache_download_dir_on_emptydir():
    text = CACHE_BASE.replace('"org/model"]', '"org/model", "--download-dir", "/models"]')
    text += "volumeMounts: [{name: c, mountPath: /models}]\n"
    found = lint_text(pod(text, "volumes:\n- name: c\n  emptyDir: {}\n")).findings
    assert rules_of(found) == ["hf-cache-ephemeral"]


def test_local_model_path_needs_no_cache():
    assert lint_text(pod(CACHE_BASE.replace("org/model", "/mnt/model"))).findings == []


def mem_case(args, image):
    text = pod(f"""
        name: v
        image: {image}
        args: {args}
        resources: {{limits: {{nvidia.com/gpu: 1}}}}
        volumeMounts: [{{name: c, mountPath: /root/.cache/huggingface}}]
    """, PVC)
    return rules_of(lint_text(text).findings)


def test_mem_fraction_ranges():
    vllm = "vllm/vllm-openai:v0.11.0"
    sglang = "lmsysorg/sglang:latest"
    flag = '["--model", "m", "--gpu-memory-utilization", "{}"]'
    assert mem_case(flag.format("1.0"), vllm) == []
    assert mem_case(flag.format("0"), vllm) == ["mem-fraction-range"]
    assert mem_case(flag.format("high"), vllm) == ["mem-fraction-range"]
    # SGLang's static fraction must stay below 1
    static = '["--model-path", "m", "--mem-fraction-static", "{}"]'
    assert mem_case(static.format("1.0"), sglang) == ["mem-fraction-range"]
    assert mem_case(static.format("0.88"), sglang) == []


def test_list_kind_and_cronjob():
    manifest = """
        apiVersion: v1
        kind: List
        items:
        - apiVersion: v1
          kind: ConfigMap
          metadata: {name: c}
          data: {a: b}
        - apiVersion: batch/v1
          kind: CronJob
          metadata: {name: nightly}
          spec:
            jobTemplate:
              spec:
                template:
                  spec:
                    containers:
                    - name: v
                      image: vllm/vllm-openai:v0.11.0
                      args: ["--model", "/m", "-tp", "2"]
                      resources: {limits: {nvidia.com/gpu: 1}}
    """
    found = lint_text(manifest).findings
    assert sorted(f.rule for f in found) == ["gpu-parallel-mismatch", "shm-missing"]
    assert found[0].kind == "CronJob"
