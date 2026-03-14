#!/usr/bin/env python3
"""
Shared SiliconFlow Embeddings API wrapper
This module provides a common embedding service for all scripts in the project
"""

import json
import requests
from langchain.embeddings.base import Embeddings


class SiliconFlowEmbeddings(Embeddings):
    
    def __init__(
        self,
        api_key: str,
        model_name: str = "BAAI/bge-large-en-v1.5",
        api_url: str = "https://api.siliconflow.cn/v1/embeddings",
    ):
        self.api_key = api_key
        self.model_name = model_name
        self.api_url = api_url
        self.headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }

    def _get_embedding(self, text: str) -> list[float]:
        payload = {
            "model": self.model_name,
            "input": text
        }
        try:
            response = requests.post(self.api_url, headers=self.headers, data=json.dumps(payload))
            response.raise_for_status()
            result = response.json()
            return result['data'][0]['embedding']
        except requests.exceptions.RequestException as e:
            print(f"API call error: {e}")
            return []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._get_embedding(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        """Embed query text"""
        return self._get_embedding(text)
