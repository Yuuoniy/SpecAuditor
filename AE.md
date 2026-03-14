# Artifact Evaluation Guide

- [Artifact Evaluation Guide](#artifact-evaluation-guide)
  - [Setup](#setup)
  - [Evaluation At A Glance](#evaluation-at-a-glance)
  - [1. Minimal Running Example](#1-minimal-running-example)
    - [Run](#run)
    - [Compare](#compare)
    - [Expected Observations](#expected-observations)
  - [2. Evaluation For Specification Generation](#2-evaluation-for-specification-generation)
    - [Run](#run-1)
    - [Compare](#compare-1)
    - [Expected Observations](#expected-observations-1)
  - [3. Evaluation For Bug Detection](#3-evaluation-for-bug-detection)
    - [Run](#run-2)
    - [Compare](#compare-2)
    - [Expected Observations](#expected-observations-2)

## Setup

Use [INSTALL.md](./INSTALL.md) first.

## Evaluation At A Glance

| Workflow                            | Script                                     | Description                                                                                                                     | Main output references                          |
| ----------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| Functional minimal example          | `artifact/functional/run.sh`               | Run the full pipeline on one seed patch and check the generated specification plus the demo bug detection result.               | `artifact/functional/reference/*.csv`           |
| specification generation | `artifact/reproduced_generation/run.sh`    | Run the packaged reproduced subset to verify that seed patches can be generalized into new concrete specifications.             | `artifact/reproduced_generation/reference/*`    |
| bug detection            | `artifact/reproduced_bug_detection/run.sh` | Run the packaged bug-detection benchmark to verify that the generated specifications can identify new bugs in the Linux kernel. | `artifact/reproduced_bug_detection/reference/*` |

## 1. Minimal Running Example

The minimal example now focuses on a single seed patch, `c158cf914713`. This patch fixes a reference-management bug around `fwnode_get_named_child_node()`.
Starting from this seed patch, SpecAuditor extracts the seed rule, generalizes it, and generates a new concrete specification for `of_slim_get_device()`. It then uses this generated specification to detect the bug in `qcom_slim_ngd_notify_slaves()`.

<img src="./artifact/functional/reference/minimal-example.png" alt="Minimal example workflow" width="320">


### Run

```bash
bash artifact/functional/run.sh \
  --kernel-path /workspace/linux-v6.17-rc3
```

### Compare

| Output file                                      | What to check                                   | Reference                                                     |
| ------------------------------------------------ | ----------------------------------------------- | ------------------------------------------------------------- |
| `step1_specifcation_extraction_*.csv`            | extracted seed specification                    | `artifact/functional/reference/stage1_reference.csv`          |
| `step2_specifcation_generalization.csv`          | generalized specification                       | `artifact/functional/reference/stage2_reference.csv`          |
| `step3_similar_target_search.csv`                | reused retrieval result                         | `artifact/functional/reference/stage3_reference.csv`          |
| `step4_specification_generation_formatted.csv`   | generated specification for the packaged target | `artifact/functional/reference/stage4_reference.csv`          |
| `bug_detection_threaded_minimal*_simplified.csv` | normal stage5 output                            | `artifact/functional/reference/stage5_pipeline_reference.csv` |
| `targeted_bug_checks.csv`                        | short final demo summary                        | `artifact/functional/reference/stage5_targeted_reference.csv` |

### Expected Observations
- SpeAuditor extracts and generalizes a reference-release specification from `c158cf914713`
- SpeAuditor  generates the new specification for `of_slim_get_device`, and detects the bug `qcom_slim_ngd_notify_slaves`
- `targeted_bug_checks.csv` should contain `c158cf914713 -> of_slim_get_device -> qcom_slim_ngd_notify_slaves`

Typical runtime is about `2-5 minutes`, depending on LLM latency.

## 2. Evaluation For Specification Generation

This reproduced subset validates the claim that SpecAuditor can generate many new concrete specifications from historical seed patches.

For AE, we use a fixed subset and its shipped references instead of the full paper-scale experiment. The packaged workflow starts from `12` seed patches. In the shipped reference, these seeds produce extracted and generalized seed specifications, and the full generation reference contains `77` generated specifications. The AE run uses a smaller subset of this workflow to keep runtime and token cost manageable while still showing that the extracted rules generalize into many mostly valid new specifications.

### Run

```bash
bash artifact/reproduced_generation/run.sh \
  --kernel-path /workspace/linux-v6.17-rc3
```

### Compare

| Output file                                    | What to check                                 | Reference                                                              |
| ---------------------------------------------- | --------------------------------------------- | ---------------------------------------------------------------------- |
| `step1_specifcation_extraction_*.csv`          | extracted seed specifications                 | `artifact/reproduced_generation/reference/seed_reference.csv`          |
| `step2_specifcation_generalization.csv`        | generalized specifications                    | `artifact/reproduced_generation/reference/seed_reference.csv`          |
| `step4_specification_generation_formatted.csv` | generated specifications for the quick subset | `artifact/reproduced_generation/reference/stage4_reference_subset.csv` |
| `reproduced_spec_generation_summary.json`      | summary counts for the packaged subset        | `artifact/reproduced_generation/reference/summary.json`                |

### Expected Observations

- SpecAuditor extracts seed specifications for the `12` packaged seeds and produce the corresponding generalized specifications
- SpecAuditor generates dozens of concrete specifications and they align semantically with the provided subset reference
- most generated rows should be semantically valid and consistent with the provided references, even if the exact wording differs
- `reproduced_spec_generation_summary.json` should be broadly consistent with `artifact/reproduced_generation/reference/summary.json`

Typical runtime is about `5-10 minutes`, depending on LLM latency and concurrent request throughput.

## 3. Evaluation For Bug Detection

This reproduced benchmark validates the claim that the extracted and generated specifications can detect real bugs.

Validating reported bugs often requires manual effort. In this artifact, we provide `48` true bug instances that we have already checked, so reviewers can automatically validate the main claim. We are still working through the remaining bugs outside this packaged benchmark.
To reduce token cost, we do not ask the LLM to inspect every possible candidate function. Instead, we directly evaluate the pre-identified buggy functions listed in `artifact/reproduced_bug_detection/datasets/checks.csv`, where each row fixes the entity, the buggy function, and the specification used for bug detection.

### Run

```bash
bash artifact/reproduced_bug_detection/run.sh \
  --kernel-path /workspace/linux-v6.17-rc3
```

### Compare

| Output file                             | What to check                                     | Reference                                                            |
| --------------------------------------- | ------------------------------------------------- | -------------------------------------------------------------------- |
| `reproduced_bug_detection_results.csv`  | one row per targeted bug check                    | `artifact/reproduced_bug_detection/reference/reference.csv`          |
| `reproduced_bug_detection_summary.csv`  | per-seed detected/evaluated counts                | `artifact/reproduced_bug_detection/reference/reference_summary.csv`  |
| `reproduced_bug_detection_summary.json` | shortest live summary                             | `artifact/reproduced_bug_detection/reference/reference_summary.json` |
| packaged bug-check dataset              | entity, buggy function, and applied specification | `artifact/reproduced_bug_detection/datasets/checks.csv`              |
| reproduced summary                      | aggregate benchmark counts and seed coverage      | `artifact/reproduced_bug_detection/reference/summary.json`           |

### Expected Observations

- the run should produce a non-empty result file and a non-empty summary
- the output should detect most of the `48` packaged bug instances
- positive rows in `reproduced_bug_detection_results.csv` should overlap strongly with `artifact/reproduced_bug_detection/reference/reference.csv`
- `reproduced_bug_detection_summary.json` should be broadly consistent with `artifact/reproduced_bug_detection/reference/reference_summary.json`

Because LLM outputs are not perfectly stable, the exact live count may vary slightly across runs. However, the live run should still detect most of these packaged bugs and should remain close to the shipped reference results.

Typical runtime is about `2-4 minutes`, depending on LLM latency.
