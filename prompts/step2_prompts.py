#!/usr/bin/env python3
"""
Step 2 Prompts for Specification Generalization
"""

# System prompt for generalizing specifications
GENERALIZE_SYSTEM = """You are an experienced vulnerability research expert skilled at generalizing specific security patch constraints into broader, reusable vulnerability detection rules.

## Task Description

Your task is to:
- Analyze the given patch code, patch description, and extracted specification.
- Generalize the Target and Predicate into more abstract, semantically meaningful forms.
- Ensure that the generalized specification remains accurate, detectable, and broadly applicable to similar code patterns.
- The generated specifications will later be used to identify similar targets and automatically generate target-specific specifications.


Each specification describes:
- Target: what kind of entity or operation (function, struct, macro, enum, type, etc.) exhibits a certain behavior that makes it potentially vulnerable.
- Predicate: what constraints or requirements such entities must follow to remain secure.


## Guidance for Target Generalization
- Analyze the semantic behavior of the target that defines the vulnerable operation.
- Ignore surface syntax or specific identifiers.
- Use short, action-oriented phrases (verb-object form) that summarize what the target does, not the entire vulnerability pattern.

Examples:
- function that Copy data from user space.
- function that Push data to user space.
- function that Increment a reference counter.
- function that May return NULL on failure.

## Guidance for Predicate Generalization
- Describe what must or must not be done when an entity exhibits the above behavior.
- Clearly specify the constraints or conditions that should hold.

Examples:
- After the call, the return value must be checked for NULL.
- When the operation fails, any acquired resource must be released.
- The object must be properly freed after use.
"""

# User prompt template for generalization
GENERALIZE_USER = """Based on the following information, generalize the given vulnerability specification into a more general and transferable security detection rule.

# Patch Description:
{commit_message}

# Patch Code:
{patch_content}

# Original Specification:
- Target: {original_target}
- Predicate: {original_predicate}

Please analyze the core vulnerable behavior addressed by this patch, and generalize the original specification so that it can effectively capture semantically similar vulnerability patterns.

Return the result strictly in the following JSON format:
{{
    "generalized_target": "Generalized description of the vulnerable target behavior.",
    "generalized_predicate": "Generalized constraint or condition corresponding to the target behavior."
}}"""


# Legacy prompts for backward compatibility
GENERATE_SPEC_SYSTEM = """你是资深漏洞研究与代码审计专家，专门从安全补丁中生成精确的漏洞检测规约。

你的任务是根据给定的目标和谓词描述，生成更详细的规约说明。"""

GENERATE_SPEC_USER = """基于以下信息生成详细规约：

目标: {target}
谓词: {predicate}
补丁信息: {patch_info}

请生成详细的规约说明。"""

if __name__ == "__main__":
    print("Step 2 Prompts loaded successfully!")