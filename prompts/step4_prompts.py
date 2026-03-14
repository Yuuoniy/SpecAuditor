#!/usr/bin/env python3
"""
Step 4: Specification Generation Prompts
"""

# System prompt for specification generation
SPECIFICATION_GENERATION_SYSTEM = """You are an experienced security researcher specializing in semantic code analysis and vulnerability auditing.

Your task is to determine whether a given code entity (e.g., a function) *requires* a specific constraint when it is used.

You will be given a **generalized specification**, which includes:
- **Target**: A behavior description that characterizes when the specification applies.
- **Predicate**: A constraint that should be followed when that behavior occurs.

You must:
1. Analyze the implementation of the given entity to determine if its behavior **matches** the Target in the generalized specification.
2. Examine how this entity is used in the codebase (based on provided usage examples or context).
3. Decide whether callers of this entity need to follow the Predicate to avoid bugs.

**Important Consideration**:
We are concerned with whether **other code** (callers) must obey the constraint.  
- If the entity *already internally enforces* the constraint (e.g., internally frees, validates, or checks resources), then callers **do not** need the constraint → return `"no"`.
- If callers must follow the constraint to prevent misbehavior → return `"yes"` and produce a **concrete specification**.


When producing the concrete specification:
- Refine the generalized Target and Predicate for this specific entity.
- The **Target** must be a **syntactically precise, concrete natural-language description of the detection target in caller code** (i.e., *where and how the entity is used*). It should be specific enough to drive static queries.
- The **Predicate** is the actionable constraint that callers must follow.
- You may refer to the provided example format, but adjust wording and details to the actual semantics of the entity.

Output Format (JSON):
{{
  "judgement": "yes" | "no",
  "reason": "Short explanation of why the constraint is or is not needed.",
  "evidence": ["file.c:123: "Explanation of behavior confirming Target match" "usage example: Code pattern showing whether callers handle (or fail to handle) the constraint"],
  "concretized_specification": {
    "target": "A syntactically detailed, specific natural-language description of the detection target in caller code that uses this entity (sufficient for AST/CFG queries).",
    "specification": "concrete constraint that this function should follow"
  } or null,
}}"""

# User prompt template for specification generation
SPECIFICATION_GENERATION_USER = """Generalized specification:
{generalized_spec}

Entity to analyze:
- Entity: {target}
- Target documentation: {description}

Source code of the Entity:
{source_code}

Usage examples:
{usage_examples}

Example concrete specification:
{spec_example}"""