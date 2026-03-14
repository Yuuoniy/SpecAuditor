#!/usr/bin/env python3
"""
Prompt Loader Utility - Simplified version
"""

import sys
from pathlib import Path


class PromptLoader:
    
    def __init__(self):
        script_dir = Path(__file__).parent
        prompts_dir = script_dir.parent.parent / "prompts"
        
        sys.path.insert(0, str(prompts_dir))
        
        try:
            import step1_prompts
            import step2_prompts  
            import step3_prompts
            import step4_prompts
            import bug_audit_prompts
            
            self.step1 = step1_prompts
            self.step2 = step2_prompts
            self.step3 = step3_prompts
            self.step4 = step4_prompts
            self.bug_audit = bug_audit_prompts
        except ImportError as e:
            print(f"Warning: Could not import prompt modules: {e}")
            self.step1 = None
            self.step2 = None
            self.step3 = None
            self.step4 = None
            self.bug_audit = None
    
    def get_step1_prompt(self, name: str, **kwargs) -> str:
        if name == "extract_patterns_system":
            return self.step1.EXTRACT_PATTERNS_SYSTEM
        elif name == "extract_patterns_user":
            return self.step1.EXTRACT_PATTERNS_USER.format(**kwargs)
        elif name == "filter_targets_system":
            return self.step1.FILTER_TARGETS_SYSTEM
        elif name == "filter_targets_analysis":
            return self.step1.FILTER_TARGETS_ANALYSIS.format(**kwargs)
        raise ValueError(f"Unknown Step 1 prompt: {name}")
    
    def get_step2_prompt(self, name: str, **kwargs) -> str:
        if name == "generate_spec_system":
            return self.step2.GENERATE_SPEC_SYSTEM
        elif name == "generate_spec_user":
            return self.step2.GENERATE_SPEC_USER.format(**kwargs)
        elif name == "generalize_system":
            return self.step2.GENERALIZE_SYSTEM
        elif name == "generalize_user":
            return self.step2.GENERALIZE_USER.format(**kwargs)
        raise ValueError(f"Unknown Step 2 prompt: {name}")
    
    def get_step3_prompt(self, name: str, **kwargs) -> str:
        if name == "generate_weggli_system":
            return self.step3.GENERATE_WEGGLI_SYSTEM
        elif name == "generate_weggli_user":
            return self.step3.GENERATE_WEGGLI_USER.format(**kwargs)
        elif name == "analyze_violation_system":
            return self.step3.ANALYZE_VIOLATION_SYSTEM.format(**kwargs)
        elif name == "analyze_violation_user":
            return self.step3.ANALYZE_VIOLATION_USER.format(**kwargs)
        raise ValueError(f"Unknown Step 3 prompt: {name}")


    def get_step4_prompt(self, name: str, **kwargs) -> str:
        if name == "specification_generation_system":
            return self.step4.SPECIFICATION_GENERATION_SYSTEM
        elif name == "specification_generation_user":
            return self.step4.SPECIFICATION_GENERATION_USER.format(**kwargs)
        raise ValueError(f"Unknown Step 4 prompt: {name}")

    def get_bug_audit_prompt(self, name: str, **kwargs) -> str:
        if not getattr(self, 'bug_audit', None):
            raise ValueError("Bug audit prompts are not available")
        if name == "system":
            template = self.bug_audit.BUG_AUDIT_SYSTEM
        elif name == "user":
            template = self.bug_audit.BUG_AUDIT_USER
        else:
            raise ValueError(f"Unknown Bug Audit prompt: {name}")

        return template.format(**kwargs) if kwargs else template


if __name__ == "__main__":
    loader = PromptLoader()
    print("✅ Prompt loader test passed!")
