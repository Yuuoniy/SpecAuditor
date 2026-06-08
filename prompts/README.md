# SpecAuditor Prompt Reference

This directory stores the active prompts used by SpecAuditor. Each prompt is a
system/user pair: the system prompt sets the role and rules, and the user prompt
fills in the runtime inputs.

## Prompts by Pipeline Stage

| Stage | System + user prompt | File | Used by | What it does | Inputs | Output |
| --- | --- | --- | --- | --- | --- | --- |
| Stage 1: Seed specification extraction | `EXTRACT_PATTERNS_SYSTEM` + `EXTRACT_PATTERNS_USER` | `step1_prompts.py` | `scripts/spec_extract.py` | Extracts a patch-grounded target and predicate from a seed bug fix. | `commit_message`, `patch_content` | JSON with `target_description`, `predicate_description` |
| Optional validation | `ANALYZE_VIOLATION_SYSTEM` + `ANALYZE_VIOLATION_USER` | `spec_validate_prompts.py` | `scripts/spec_validator.py` | Checks whether code violates a complete specification. | `specification`, `match_name`, `match_code` | JSON-style decision, reasoning, confidence |
| Stage 2: Specification generalization | `GENERALIZE_SYSTEM` + `GENERALIZE_USER` | `step2_prompts.py` | `scripts/spec_generalize.py` | Generalizes the seed target and predicate into a reusable rule. | `commit_message`, `patch_content`, `original_target`, `original_predicate` | JSON with `generalized_target`, `generalized_predicate` |
| Stage 3: Similar target retrieval | No LLM prompt | N/A | `scripts/similar_target_search.py` | Retrieves related entities with documentation search and embeddings. | Stage 2 CSV rows | Stage 3 CSV rows |
| Stage 4: Concrete specification generation | `SPECIFICATION_GENERATION_SYSTEM` + `SPECIFICATION_GENERATION_USER` | `step4_prompts.py` | `scripts/spec_generation.py` | Decides whether a retrieved entity needs the generalized constraint, then writes a concrete specification if needed. | `generalized_spec`, `target`, `description`, `source_code`, `usage_examples`, `spec_example` | JSON with `judgement`, `reason`, `evidence`, `concretized_specification` |
| Stage 5a: Candidate localization | `GENERATE_WEGGLI_SYSTEM` + `GENERATE_WEGGLI_USER` | `step3_prompts.py` | `scripts/bug_detection_threaded.py` | Generates a simple weggli query for finding candidate code. | `func_name`, `target_description` | One weggli pattern |
| Stage 5b: Violation analysis | `ANALYZE_VIOLATION_SYSTEM` + `ANALYZE_VIOLATION_USER` | `step3_prompts.py` | `scripts/bug_detection_threaded.py` | Audits candidate code against the security rule. | `func_name`, `predicate`, `match_name`, `match_code` | Decision, reasoning, confidence text |
| Standalone report pruning | `BUG_AUDIT_SYSTEM` + `BUG_AUDIT_USER` | `bug_audit_prompts.py` | `scripts/report_pruning.py` | Re-audits reported findings against the original patch root cause. | violation metadata, spec, patch context, source code, prior context | JSON `final_decision` or `more_context` |

## Flow

1. `spec_extract.py` extracts a seed target and predicate from a patch.
2. `spec_validator.py` can optionally validate that seed specification.
3. `spec_generalize.py` turns the seed rule into a broader rule.
4. `similar_target_search.py` finds related entities without an LLM prompt.
5. `spec_generation.py` creates concrete specifications for matching entities.
6. `bug_detection_threaded.py` localizes candidates with weggli, then audits them.
7. `report_pruning.py` can separately re-check reported findings.

