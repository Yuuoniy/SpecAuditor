#!/usr/bin/env python3
"""
Specification validation prompts.
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
