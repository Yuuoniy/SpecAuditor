# SpecAuditor: Generating Audit Specifications for LLM-Driven Bug Detection


SpecAuditor is an end-to-end framework for generating audit specifications to guide LLM-driven bug detection.

The main motivation behind SpecAuditor is that direct LLM auditing is often ineffective and expensive. Without explicit guidance, LLMs tend to focus on familiar surface patterns and struggle to reason about project-specific semantics and rarer bug behaviors. SpecAuditor addresses this by automatically constructing audit specifications that tell the model:

- where to audit
- how to check

At a high level, SpecAuditor follows the three-stage design:

1. extract seed specifications from bug patches
2. generalize the seed specifications, retrieve semantically related entities from documentation, and generate new concrete specifications
3. use the generated specifications to drive code search and LLM-driven bug auditing



## Pipeline

| Stage                         | Goal                                                                                          | Main scripts                                                                                                                                |
| ----------------------------- | --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Seed extraction               | Extract a target (entity) and predicate from a given patch and validate it                    | `scripts/spec_extract.py`, `scripts/spec_validator.py`                                                                                      |
| Generalization and generation | Generalize the seed rule, retrieve related entities, and generate new concrete specifications | `scripts/spec_generalize.py`, `scripts/similar_target_search.py`, `scripts/spec_generation.py`, `scripts/format_spec_generation_results.py` |
| Guided bug detection          | Localize relevant code and audit it with the generated specifications                         | `scripts/bug_detection_threaded.py`                                                                                                         |

## Repository Map

| Path        | Purpose                                                          |
| ----------- | ---------------------------------------------------------------- |
| `scripts/`  | Main SpecAuditor pipeline                                        |
| `prompts/`  | Prompt templates used by all stages                              |
| `get_docs/` | Offline documentation cache and prebuilt Chroma store            |
| `artifact/` | scripts, datasets, and reference outputs for artifact evaluation |

## Installation
We provide a Dockerfile to set up the environment. Please refer to [INSTALL.md](./INSTALL.md)  for details.

## Testing and Artifact Evaluation


Please refer to  [AE.md](./AE.md) for detailed instructions.

| Entry                                                         | Purpose                                                                                                                |
| ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `artifact/functional/run.sh`                                  | End-to-end functional run on one seed patch                                                                            |
| `artifact/reproduced_generation/run.sh`                       | Reproduced extraction/generalization/generation run on subset                                                          |
| `artifact/reproduced_bug_detection/run.sh`                    | Reproduced bug-detection benchmark with integrated candidate localization and bug auditing                             |
| `artifact/reproduced_bug_detection/run_localization_check.sh` | Localization-only check that tests whether the expected buggy functions can be automatically surfaced before auditing. |
