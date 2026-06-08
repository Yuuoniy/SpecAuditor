# SpecAuditor Prompt Reference

This directory stores the active prompts used by SpecAuditor. Each prompt is a
system/user pair: the system prompt sets the role and rules, and the user prompt
fills in the runtime inputs.

## Overview

| Stage | Prompt pair | File | Purpose |
| --- | --- | --- | --- |
| Stage 1: Seed specification extraction | `EXTRACT_PATTERNS_*` | `step1_prompts.py` | Extract a seed target and predicate from a patch. |
| Optional validation | `ANALYZE_VIOLATION_*` | `spec_validate_prompts.py` | Check whether code violates a complete specification. |
| Stage 2: Specification generalization | `GENERALIZE_*` | `step2_prompts.py` | Generalize the seed rule into a reusable rule. |
| Stage 3: Similar target retrieval | No LLM prompt | N/A | Retrieve related entities with search and embeddings. |
| Stage 4: Concrete specification generation | `SPECIFICATION_GENERATION_*` | `step4_prompts.py` | Create concrete specifications for retrieved entities. |
| Stage 5a: Candidate localization | `GENERATE_WEGGLI_*` | `step3_prompts.py` | Generate a weggli query for candidate search. |
| Stage 5b: Violation analysis | `ANALYZE_VIOLATION_*` | `step3_prompts.py` | Audit candidate code against the security rule. |
| Standalone report pruning | `BUG_AUDIT_*` | `bug_audit_prompts.py` | Re-audit reported findings. |

## Prompt Details

### Stage 1: Seed Specification Extraction

- Prompt pair: `EXTRACT_PATTERNS_SYSTEM` + `EXTRACT_PATTERNS_USER`
- Used by: `scripts/spec_extract.py`
- Inputs: `commit_message`, `patch_content`
- Output: JSON with `target_description`, `predicate_description`
- Purpose: Extracts one patch-grounded target and predicate from a seed bug fix.

### Optional Validation

- Prompt pair: `ANALYZE_VIOLATION_SYSTEM` + `ANALYZE_VIOLATION_USER`
- File: `spec_validate_prompts.py`
- Used by: `scripts/spec_validator.py`
- Inputs: `specification`, `match_name`, `match_code`
- Output: JSON-style decision, reasoning, confidence
- Purpose: Checks whether function-level code violates a complete specification.

### Stage 2: Specification Generalization

- Prompt pair: `GENERALIZE_SYSTEM` + `GENERALIZE_USER`
- Used by: `scripts/spec_generalize.py`
- Inputs: `commit_message`, `patch_content`, `original_target`, `original_predicate`
- Output: JSON with `generalized_target`, `generalized_predicate`
- Purpose: Converts a concrete seed rule into a reusable semantic rule.

### Stage 3: Similar Target Retrieval

- Prompt pair: none
- Used by: `scripts/similar_target_search.py`
- Inputs: Stage 2 CSV rows
- Output: Stage 3 CSV rows
- Purpose: Retrieves related entities with documentation search and embeddings.

### Stage 4: Concrete Specification Generation

- Prompt pair: `SPECIFICATION_GENERATION_SYSTEM` + `SPECIFICATION_GENERATION_USER`
- Used by: `scripts/spec_generation.py`
- Inputs: `generalized_spec`, `target`, `description`, `source_code`, `usage_examples`, `spec_example`
- Output: JSON with `judgement`, `reason`, `evidence`, `concretized_specification`
- Purpose: Decides whether a retrieved entity needs the generalized constraint, then writes a concrete specification if needed.

### Stage 5a: Candidate Localization

- Prompt pair: `GENERATE_WEGGLI_SYSTEM` + `GENERATE_WEGGLI_USER`
- Used by: `scripts/bug_detection_threaded.py`
- Inputs: `func_name`, `target_description`
- Output: one weggli pattern
- Purpose: Generates a simple weggli query for finding candidate code.

### Stage 5b: Violation Analysis

- Prompt pair: `ANALYZE_VIOLATION_SYSTEM` + `ANALYZE_VIOLATION_USER`
- Used by: `scripts/bug_detection_threaded.py`
- Inputs: `func_name`, `predicate`, `match_name`, `match_code`
- Output: decision, reasoning, confidence text
- Purpose: Audits candidate code against the generated security rule.

### Standalone Report Pruning

- Prompt pair: `BUG_AUDIT_SYSTEM` + `BUG_AUDIT_USER`
- Used by: `scripts/report_pruning.py`
- Inputs: violation metadata, specification, patch context, source code, prior context
- Output: JSON `final_decision` or `more_context`
- Purpose: Re-checks reported findings against the original patch root cause.
