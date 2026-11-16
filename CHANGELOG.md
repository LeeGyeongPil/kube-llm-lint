# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-11-16

### Added
- `--format sarif` prints a SARIF 2.1.0 log (standard library only); exit codes are unchanged.

## [0.1.1] - 2026-11-16

### Fixed
- Probe checks no longer skip silently when `initialDelaySeconds`, `periodSeconds` or `failureThreshold` is YAML null or valueless (Kubernetes defaults 0/10/3 apply); an empty string `""` or non-numeric values are reported as `arg-unresolved`.

## [0.1.0] - 2026-11-16

### Added
- `kube-llm-lint` command: checks vLLM, SGLang and TGI containers in Kubernetes manifests.
- Input from files, directories (recursive) and stdin (`-`), multi-document YAML and `kind: List`.
- Workloads: Pod, Deployment, StatefulSet, DaemonSet, ReplicaSet, Job, CronJob, LeaderWorkerSet.
- Server argument reading: `sh -c` strings, command lists, `--k=v`, short aliases,
  `python -m` entry points, `$(VAR)` and `$VAR` from literal env values, TGI `NUM_SHARD`/`MODEL_ID`.
- Rules: `gpu-parallel-mismatch`, `gpu-missing`, `gpu-mig-unchecked`, `shm-missing`,
  `shm-not-memory`, `shm-too-small`, `no-startup-probe`, `probe-budget-short`,
  `hf-cache-ephemeral`, `hf-token-plaintext`, `mem-fraction-range`,
  `config-file-unchecked`, `arg-unresolved`.
- Paste-ready YAML fixes for each finding.
- `--format json`, `--fail-on`, `--startup-budget`, `--disable`, `--list-rules`,
  and the `kube-llm-lint/ignore` annotation.
- English and Korean output (`--lang`, `KUBE_LLM_LINT_LANG`, `LC_ALL`, `LC_MESSAGES`, `LANG`).

### Fixed
- Containers with a custom `command` that does not start a known server (Ray workers, start
  scripts) are no longer judged from the image name, which gave false `gpu-parallel-mismatch`.
- vLLM flags written with underscores (`--tensor_parallel_size`) are read like the dash form.
- Argument errors and non-UTF-8 input are reported in the selected language with a fix hint.
- Output wording has no singular/plural errors; empty files are counted; a directory without
  YAML files exits 2.
