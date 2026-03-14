#!/usr/bin/env python3
"""
OpenAI-compatible LLM client with YAML configuration support
"""

import os
import sys
import json
import time
import yaml
from pathlib import Path
from typing import Optional, Dict, Any, List

try:
    from openai import OpenAI
    import openai as legacy_openai
except ImportError:
    OpenAI = None
    import openai as legacy_openai

try:
    from .artifact_utils import resolve_model_config
except ImportError:
    from artifact_utils import resolve_model_config

class OpenAIClient:

    def _resolve_model_name(self, model: Optional[str]) -> str:
        models = self.config.get("models", {})
        if not model:
            default_model = self.config.get("default_model", "gpt-4o-mini")
            if default_model in models:
                return default_model
            raise ValueError("No model specified and no valid default model found in configuration")

        if model in models:
            return model

        default_model = self.config.get("default_model", "gpt-4o-mini")
        if default_model in models:
            print(f"Model '{model}' not found, using default: {default_model}")
            return default_model

        raise ValueError(f"Model '{model}' not configured and no valid default found")

    def __init__(self, 
                 model: Optional[str] = None,
                 system_prompt: str = "",
                 config_path: Optional[str] = None):
        
        if config_path is None:
            config_path = Path(__file__).parent / "openai_config.yaml"
        
        self.config = self._load_config(config_path)
        self.model = self._resolve_model_name(model)
        self.system_prompt = system_prompt
        
        model_config = resolve_model_config(self._get_model_config(self.model))
        
        self.api_key = model_config["api_key"]
        self.base_url = model_config.get("base_url")
        self.temperature = model_config.get("temperature", 0)
        self.max_tokens = model_config.get("max_tokens")
        self.max_retries = max(1, model_config.get("max_retries", 3))
        self.request_timeout = model_config.get("request_timeout", 60)
        
        if not self.api_key:
            raise ValueError("API key is required")
        
        self._init_client()
    
    def _load_config(self, config_path: Path) -> Dict[str, Any]:
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML configuration: {e}")
    
    def _get_model_config(self, model: str) -> Dict[str, Any]:
        models = self.config.get("models", {})
        return models[self._resolve_model_name(model)]
    
    def set_system_prompt(self, system_prompt: str):
        self.system_prompt = system_prompt
    
    def set_temperature(self, temperature: float):
        if not 0 <= temperature <= 2:
            raise ValueError("Temperature must be between 0 and 2")
        self.temperature = temperature
    
    def set_model(self, model: str):
        self.model = self._resolve_model_name(model)
        model_config = resolve_model_config(self._get_model_config(self.model))
        
        self.api_key = model_config["api_key"]
        self.base_url = model_config.get("base_url")
        self.temperature = model_config.get("temperature", self.temperature)
        self.max_tokens = model_config.get("max_tokens", self.max_tokens)
        self.max_retries = max(1, model_config.get("max_retries", 3))
        self.request_timeout = model_config.get("request_timeout", 60)
        
        self._init_client()

    def _init_client(self):
        if OpenAI is not None:
            client_kwargs = {"api_key": self.api_key}
            if self.base_url:
                client_kwargs["base_url"] = self.base_url
            self.client = OpenAI(**client_kwargs)
            self.uses_legacy_sdk = False
            return

        legacy_openai.api_key = self.api_key
        if self.base_url:
            legacy_openai.api_base = self.base_url
        self.client = legacy_openai
        self.uses_legacy_sdk = True

    def _create_chat_completion(self, messages, temperature):
        if self.uses_legacy_sdk:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "request_timeout": self.request_timeout,
            }
            if self.max_tokens is not None:
                kwargs["max_tokens"] = self.max_tokens
            return self.client.ChatCompletion.create(**kwargs)

        return self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=self.max_tokens,
            timeout=self.request_timeout,
        )

    def _extract_response_content(self, response):
        if self.uses_legacy_sdk:
            return response["choices"][0]["message"]["content"]
        return response.choices[0].message.content

    def _extract_usage(self, response):
        if self.uses_legacy_sdk:
            usage = response.get("usage", {}) or {}
            return usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)

        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        return input_tokens, output_tokens
    
    def set_max_tokens(self, max_tokens: Optional[int]):
        self.max_tokens = max_tokens
    
    def _remove_think_tags(self, content: str) -> str:
        import re
        pattern = r'<think>.*?</think>'
        cleaned_content = re.sub(pattern, '', content, flags=re.DOTALL)
        cleaned_content = re.sub(r'\n\s*\n\s*\n', '\n\n', cleaned_content)
        return cleaned_content.strip()
    
    def send_message(self, 
                    user_prompt: str,
                    system_prompt: Optional[str] = None,
                    temperature: Optional[float] = None) -> str:
        """
        Send message to LLM
        
        Args:
            user_prompt: User prompt
            system_prompt: System prompt (optional, overrides default)
            temperature: Temperature parameter (optional, overrides default)
            
        Returns:
            Model response content
        """
        try:
            current_system_prompt = system_prompt if system_prompt is not None else self.system_prompt
            current_temperature = temperature if temperature is not None else self.temperature
            
            messages = []
            if current_system_prompt:
                messages.append({"role": "system", "content": current_system_prompt})
            messages.append({"role": "user", "content": user_prompt})
            
            last_error: Optional[Exception] = None
            for attempt in range(self.max_retries):
                try:
                    response = self._create_chat_completion(messages, current_temperature)
                    content = self._extract_response_content(response)
                    cleaned_content = self._remove_think_tags(content)
                    return cleaned_content
                except Exception as e:
                    last_error = e
                    wait_seconds = min(2 ** attempt, 10)
                    if attempt < self.max_retries - 1:
                        print(f"API call error: {e}, retrying in {wait_seconds}s ({attempt + 1}/{self.max_retries})")
                        time.sleep(wait_seconds)
                    else:
                        print(f"API call error: {e}")
            return 'unknown'
        except Exception as e:
            print(f"API call error: {e}")
            return 'unknown'
    
    def send_message_with_tokens(self, 
                                user_prompt: str,
                                system_prompt: Optional[str] = None,
                                temperature: Optional[float] = None) -> tuple:
        """
        Send message and return both content and token usage
        
        Args:
            user_prompt: User prompt
            system_prompt: System prompt (optional, overrides default)
            temperature: Temperature parameter (optional, overrides default)
            
        Returns:
            Tuple of (response_content, input_tokens, output_tokens)
        """
        try:
            current_system_prompt = system_prompt if system_prompt is not None else self.system_prompt
            current_temperature = temperature if temperature is not None else self.temperature
            
            messages = []
            if current_system_prompt:
                messages.append({"role": "system", "content": current_system_prompt})
            messages.append({"role": "user", "content": user_prompt})
            
            last_error: Optional[Exception] = None
            for attempt in range(self.max_retries):
                try:
                    response = self._create_chat_completion(messages, current_temperature)
                    content = self._extract_response_content(response)
                    cleaned_content = self._remove_think_tags(content)
                    input_tokens, output_tokens = self._extract_usage(response)
                    return cleaned_content, input_tokens, output_tokens
                except Exception as e:
                    last_error = e
                    wait_seconds = min(2 ** attempt, 10)
                    if attempt < self.max_retries - 1:
                        print(f"API call error: {e}, retrying in {wait_seconds}s ({attempt + 1}/{self.max_retries})")
                        time.sleep(wait_seconds)
                    else:
                        print(f"API call error: {e}")
            return 'unknown', 0, 0
        except Exception as e:
            print(f"API call error: {e}")
            return 'unknown', 0, 0
    
    def get_config(self) -> Dict[str, Any]:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "system_prompt": self.system_prompt[:100] + "..." if len(self.system_prompt) > 100 else self.system_prompt
        }
    
    def print_config(self):
        config = self.get_config()
        print("OpenAI Client Configuration:")
        print("=" * 50)
        for key, value in config.items():
            print(f"  {key}: {value}")
        print("=" * 50)


if __name__ == "__main__":
    client = OpenAIClient()
    client.print_config()
