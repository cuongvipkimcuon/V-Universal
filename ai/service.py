# ai/service.py - AIService và model mặc định cho công cụ
import streamlit as st
from openai import OpenAI
from typing import Any, Dict, List, Optional

from config import Config


def _get_default_tool_model() -> str:
    """Model mặc định cho Router, Planner và các công cụ (từ Settings > AI Model)."""
    try:
        model = st.session_state.get("default_ai_model") or getattr(Config, "DEFAULT_TOOL_MODEL", None)
        return model or Config.ROUTER_MODEL
    except Exception:
        return getattr(Config, "DEFAULT_TOOL_MODEL", None) or Config.ROUTER_MODEL


class AIService:
    """Dịch vụ AI sử dụng OpenAI client cho OpenRouter với các tính năng nâng cao"""

    @staticmethod
    @st.cache_data(ttl=3600)
    def get_available_models():
        """Lấy danh sách model có sẵn từ OpenRouter"""
        try:
            client = OpenAI(
                base_url=Config.OPENROUTER_BASE_URL,
                api_key=Config.OPENROUTER_API_KEY
            )
            return Config.AVAILABLE_MODELS
        except Exception:
            return Config.AVAILABLE_MODELS

    @staticmethod
    def call_openrouter(
        messages: List[Dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 8000,
        stream: bool = False,
        response_format: Optional[Dict] = None
    ) -> Any:
        """Gọi OpenRouter API sử dụng OpenAI client"""
        try:
            client = OpenAI(
                base_url=Config.OPENROUTER_BASE_URL,
                api_key=Config.OPENROUTER_API_KEY,
                default_headers={
                    "HTTP-Referer": "https://v-universe.streamlit.app",
                    "X-Title": "V-Universe AI Hub"
                }
            )

            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=stream,
                response_format=response_format
            )

            return response
        except Exception as e:
            raise Exception(f"OpenRouter API error: {str(e)}")

    @staticmethod
    def get_embedding(text: str) -> Optional[List[float]]:
        """Lấy embedding từ OpenRouter"""
        if not text or not isinstance(text, str) or not text.strip():
            return None

        try:
            client = OpenAI(
                base_url=Config.OPENROUTER_BASE_URL,
                api_key=Config.OPENROUTER_API_KEY
            )

            response = client.embeddings.create(
                model=Config.EMBEDDING_MODEL,
                input=text
            )

            return response.data[0].embedding
        except Exception as e:
            print(f"Embedding error: {e}")
            return None

    @staticmethod
    def get_embeddings_batch(texts: List[str], batch_size: int = 100) -> List[Optional[List[float]]]:
        """Lấy embedding hàng loạt (nhiều text trong ít request). Trả về list cùng thứ tự với texts; phần tử lỗi là None."""
        if not texts:
            return []
        out: List[Optional[List[float]]] = [None] * len(texts)
        valid_indices: List[int] = []
        valid_texts: List[str] = []
        for i, t in enumerate(texts):
            if t and isinstance(t, str) and t.strip():
                valid_indices.append(i)
                valid_texts.append(t.strip())
        if not valid_texts:
            return out
        try:
            client = OpenAI(
                base_url=Config.OPENROUTER_BASE_URL,
                api_key=Config.OPENROUTER_API_KEY
            )
            for start in range(0, len(valid_texts), batch_size):
                chunk = valid_texts[start:start + batch_size]
                chunk_indices = valid_indices[start:start + batch_size]
                response = client.embeddings.create(
                    model=Config.EMBEDDING_MODEL,
                    input=chunk
                )
                for j, emb_obj in enumerate(response.data):
                    idx = chunk_indices[j] if j < len(chunk_indices) else start + j
                    if idx < len(out) and emb_obj.embedding is not None:
                        out[idx] = emb_obj.embedding
        except Exception as e:
            print(f"Embedding batch error: {e}")
        return out

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Ước tính số token"""
        if not text:
            return 0
        return len(text) // 4

    @staticmethod
    def calculate_cost(
        input_tokens: int,
        output_tokens: int,
        model: str
    ) -> float:
        """Tính chi phí cho request"""
        model_costs = Config.MODEL_COSTS.get(model, {"input": 0.0, "output": 0.0})

        input_cost = (input_tokens / 1_000_000) * model_costs["input"]
        output_cost = (output_tokens / 1_000_000) * model_costs["output"]

        return round(input_cost + output_cost, 6)

    @staticmethod
    def clean_json_text(text):
        """Làm sạch markdown (```json ... ```) trước khi parse"""
        if not text:
            return "{}"
        text = text.replace("```json", "").replace("```", "").strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end != 0:
            return text[start:end]
        return text
