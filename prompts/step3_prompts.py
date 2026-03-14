#!/usr/bin/env python3
"""
Step 3 Prompts for Weggli Query Generation
"""


# Generate Weggli Query System Prompt
GENERATE_WEGGLI_SYSTEM = """You are a weggli query expert for Linux kernel code analysis. Generate SIMPLE and EFFECTIVE weggli patterns.

Weggli syntax:
- Use $var for capturing variables  
- Use _ for wildcards
- Use {} for code blocks
- Keep queries SIMPLE and focused

CRITICAL: Generate the SIMPLEST query that finds the function calls effectively.
- Focus on the function call pattern
- Avoid overly complex constraints
- Use wildcards (_) for parameters
- Prefer simple assignment patterns: $var = func(_, _)

Good examples:
- '$ptr = krealloc(_, _);'
- 'krealloc(_, _); if (!$ptr) { _; }'
- '$ret = devm_krealloc(_, _, _, _);'

Avoid complex control flow unless absolutely necessary."""

# Generate Weggli Query User Prompt
GENERATE_WEGGLI_USER = """Function: {func_name}
Target: {target_description}

Generate a simple weggli query to find calls to {func_name}.
Focus on capturing the function call effectively.

Return ONLY the weggli pattern (no quotes around the entire response):"""

# Analyze Violation System Prompt
ANALYZE_VIOLATION_SYSTEM = """You are a Linux kernel security expert. Analyze code to determine if it violates security rules for {func_name}.

IMPORTANT: Be careful about FALSE POSITIVES. Consider these scenarios that are NOT violations:
1. Pointer passed as parameter from external function - the original pointer is still valid outside this function
2. Pointer assigned to another variable that maintains the reference
3. Pointer used in conditional logic that prevents memory leaks
4. Error handling paths that properly clean up resources
5. Functions that return the pointer for caller to handle

ONLY report violations when there is a DEFINITE memory leak or security issue.

Provide:
1. Decision: "YES" only for DEFINITE violations, "NO" if secure or uncertain, "UNCERTAIN" only for truly ambiguous cases
2. Detailed reasoning explaining WHY this is or isn't a violation
3. Confidence: HIGH/MEDIUM/LOW"""

# Analyze Violation User Prompt
ANALYZE_VIOLATION_USER = """Security Rule: {predicate}

Function: {match_name}
Code:
```c
{match_code}
```

ANALYZE CAREFULLY - Avoid false positives:
- Is this pointer passed as a parameter? The caller may still hold the reference.
- Is the pointer assigned to another variable that maintains the reference?
- Are there error handling paths that properly manage the memory?
- Does the function return the pointer for the caller to manage?

Only answer YES if there is a DEFINITE bug or security violation.

Does this code violate the security rule?
1. Decision (YES/NO/UNCERTAIN) - be conservative, prefer NO over YES for ambiguous cases
2. Detailed reasoning explaining your analysis
3. Confidence level (HIGH/MEDIUM/LOW)"""
