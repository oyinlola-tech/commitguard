# Reproducibility

Everything here is meant to be re-run by someone who is not the author. This page
says exactly how, what is guaranteed to come out the same, and what cannot.

## Re-running everything

```bash
git clone https://github.com/oyinlola-tech/commitguard && cd commitguard
python -m pip install -e ".[dev]"

commitguard reproduce all --evidence-dir evidence/security --results benchmarks/results
commitguard report security --results benchmarks/results --evidence evidence/security
```

`reproduce` reports `PASS`, `FAIL`, `SKIPPED` (with the reason) or `NOT RUN` per
step. A skipped step is never a pass: without GitHub credentials you get

```text
SKIPPED  github   GitHub App configuration and permissions
         GitHub credentials not configured (COMMITGUARD_GITHUB_APP_ID, ... not set)
```

## What is bit-for-bit reproducible

| Artefact | Guarantee | How to check |
|---|---|---|
| The labelled dataset | a version always rebuilds to the same fingerprint | `commitguard reproduce benchmark --results benchmarks/results` compares the rebuild with the published files |
| Dataset 1.0.0 | `d1504f41205955c6…` (9,115 cases) | as above, with `--dataset-version 1.0.0` |
| Dataset 1.1.0 | `277ab03f201787dd…` (9,142 cases) | |
| Dataset 1.2.0 | `f6ef4d6a8df83aa9…` (9,157 cases) | |
| Dataset 1.3.0 | `e0592f4287b13619…` (9,174 cases) | |
| Detection accuracy | deterministic for a given dataset and code revision | `commitguard benchmark detection --dataset-version <v>` |
| Fuzzing examples | derandomized by default: the same examples every run | `pytest -m security` |
| Experiment outcomes | deterministic | `commitguard reproduce security` |

## What is not reproducible, and why

Timings. They depend on the CPU, the operating system, the Python build and what
else the machine is doing. The recorded numbers are labelled with the machine that
produced them, and the hook-overhead figures were recorded while other work was
running - stated on the page rather than hidden.

What *is* reproducible about performance is the **shape**: linear scaling in
message size and history length, and the fact that hook overhead is dominated by
interpreter start-up rather than analysis. Those conclusions hold on any machine.

## The manifest

Every recorded result carries one, so a number can always be traced to the
conditions that produced it:

```json
{
  "commitguard_version": "0.1.0.dev0",
  "source_revision": "a89d5915fd34352bcc3568049ebc6826de2cfb2e",
  "source_dirty": true,
  "python_version": "3.13.15",
  "python_implementation": "CPython",
  "git_version": "2.53.0",
  "operating_system": "Linux",
  "os_release": "7.1.5+kali-amd64",
  "machine": "x86_64",
  "cpu_model": "Intel(R) Core(TM) i5-8350U CPU @ 1.70GHz",
  "cpu_count": 8,
  "memory_bytes": 16571756544,
  "rules_version": "...",
  "policy_version": "...",
  "dataset_version": "1.3.0",
  "dataset_fingerprint": "e0592f4287b13619...",
  "command": "commitguard benchmark detection --record benchmarks/results",
  "timestamp": "2026-09-17T15:15:23.091829Z"
}
```

`source_dirty` is recorded honestly: several results here were measured from a
working tree with uncommitted changes, and say so.

## Results are immutable

`benchmarks/results/raw/` is append-only; writing a result never overwrites one.
That is why thirteen detection runs exist, including the three that failed. An
improvement is only credible if the worse number is still there.

## Reproduction checklist for an independent evaluator

1. Clone at a stated commit; `git rev-parse HEAD` and compare with the manifests.
2. `commitguard reproduce benchmark` - rebuilds the dataset, checks the
   fingerprint, re-measures detection. No credentials needed, about 10 seconds.
3. `commitguard reproduce security` - 350 tests, about 70 seconds here.
4. `commitguard benchmark performance --quick` and compare **shapes**, not
   absolute values.
5. `commitguard benchmark platform` on your own OS - this is the most valuable
   thing an external evaluator can contribute, because the macOS and Windows rows
   of the matrix are still pending.
6. Report what you found:
   [../community/external-evaluation.md](../community/external-evaluation.md).
