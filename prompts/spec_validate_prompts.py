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
- Prefer simple assignment patterns: $var = func();

Good examples:
- '$ptr = krealloc();'
- 'krealloc(); if (!$ptr) { _; }'
- '$ret = devm_krealloc();'

Avoid complex control flow unless absolutely necessary."""

# Generate Weggli Query User Prompt
GENERATE_WEGGLI_USER = """Function: {func_name}
Target: {target_description}

Generate a simple weggli query to find calls to {func_name}.
Focus on capturing the function call effectively.

Return ONLY the weggli pattern (no quotes around the entire response):
"""



ANALYZE_VIOLATION_SYSTEM = """You are a security analysis expert. Your task is to analyze C code and determine if it violates a given security specification.

ANALYSIS APPROACH:
1. Understand the security specification clearly
2. Examine the code for potential violations


RESPONSE FORMAT:
1. Decision: "YES" for definite security violations,"NO" if secure or uncertain,
2. Reasoning: Detailed explanation of your analysis
3. Confidence: HIGH/MEDIUM/LOW based on certainty of your analysis"""


ANALYZE_VIOLATION_USER = """specification: {specification}

Function: {match_name}
Code:
```c
{match_code}
```

Output Format:
{{
    "decision": "[YES/NO/UNCERTAIN]",
    "reasoning": "[detailed explanation]",
    "confidence": "[HIGH/MEDIUM/LOW]"
}}
"""
