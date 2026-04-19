"""DashScope Embedding 调用封装"""
from __future__ import annotations

import time
from typing import Optional

import numpy as np
from dashscope import MultiModalEmbedding, TextEmbedding
from loguru import logger

from config.settings import settings


class DashScopeEmbedder:
    """DashScope 文本嵌入封装"""

    def __init__(
        self,
        model_name: Optional[str] = None,
        dimension: Optional[int] = None,
        batch_size: Optional[int] = None,
        batch_interval: Optional[float] = None,
    ):
        cfg = settings.models.embedding
        self.model_name = model_name or cfg.name
        self.dimension = dimension or cfg.dimension
        self.batch_size = self._effective_batch_size(batch_size or cfg.batch_size)
        self.batch_interval = batch_interval or cfg.batch_interval

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """批量文本嵌入，返回 numpy 数组 (n, dimension)"""
        if not texts:
            return np.array([])

        all_embeddings = []
        total = len(texts)

        for i in range(0, total, self.batch_size):
            batch = texts[i : i + self.batch_size]
            logger.debug(f"Embedding 批次 {i // self.batch_size + 1}: {len(batch)} 条")

            try:
                if self._is_multimodal_embedding_model:
                    batch_embeddings = self._embed_with_multimodal(batch)
                else:
                    batch_embeddings = self._embed_with_text_embedding(batch)

                all_embeddings.extend(batch_embeddings)
                # 更新实际维度（不同模型可能返回不同维度）
                if not hasattr(self, '_actual_dim') and batch_embeddings:
                    self._actual_dim = len(batch_embeddings[0])

            except Exception as e:
                logger.error(f"Embedding 请求异常: {e}")
                if self._is_quota_error(e):
                    raise
                all_embeddings.extend([[0.0] * self.dimension] * len(batch))

            # 批次间隔（限流）
            if i + self.batch_size < total:
                time.sleep(self.batch_interval)

        # 如果实际维度与配置不同，更新 dimension
        if hasattr(self, '_actual_dim') and self._actual_dim != self.dimension:
            logger.info(f"Embedding 实际维度: {self._actual_dim} (配置: {self.dimension})")
            self.dimension = self._actual_dim

        return np.array(all_embeddings, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        """单条文本嵌入（用于查询）"""
        result = self.embed_texts([text])
        return result[0] if len(result) > 0 else np.zeros(self.dimension, dtype=np.float32)

    @property
    def _is_multimodal_embedding_model(self) -> bool:
        """qwen3-vl-embedding 等多模态向量模型需走 MultiModalEmbedding 接口。"""
        name = self.model_name.lower()
        return "vl-embedding" in name or "multimodal-embedding" in name

    def _effective_batch_size(self, configured: int) -> int:
        """根据模型接口限制修正批量大小。"""
        if self._is_multimodal_embedding_name(self.model_name):
            # qwen3-vl-embedding 单次请求 contents 元素总数不超过 20。
            return max(1, min(configured, 20))
        return max(1, configured)

    @staticmethod
    def _is_multimodal_embedding_name(model_name: str) -> bool:
        name = model_name.lower()
        return "vl-embedding" in name or "multimodal-embedding" in name

    def _embed_with_text_embedding(self, batch: list[str]) -> list[list[float]]:
        """通过通用文本向量接口生成向量。"""
        kwargs = {
            "model": self.model_name,
            "input": batch,
        }
        # v1/v2/async-v1/async-v2 不支持 dimension 参数，v3/v4 支持
        if not self.model_name.endswith(("-v1", "-v2")):
            kwargs["dimension"] = self.dimension

        resp = TextEmbedding.call(**kwargs)
        self._raise_for_embedding_error(resp)
        embeddings = resp.output["embeddings"]
        embeddings = sorted(embeddings, key=lambda item: item.get("text_index", item.get("index", 0)))
        return [item["embedding"] for item in embeddings]

    def _embed_with_multimodal(self, batch: list[str]) -> list[list[float]]:
        """通过多模态向量接口生成文本向量，支持 qwen3-vl-embedding 免费额度。"""
        input_data = [{"text": text} for text in batch]
        resp = MultiModalEmbedding.call(
            api_key=settings.dashscope_api_key,
            model=self.model_name,
            input=input_data,
            dimension=self.dimension,
        )
        self._raise_for_embedding_error(resp)
        embeddings = resp.output["embeddings"]
        embeddings = sorted(embeddings, key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in embeddings]

    def _raise_for_embedding_error(self, resp):
        if resp.status_code == 200:
            return

        error_msg = resp.message or ""
        if resp.status_code == 403 or "exhausted" in error_msg.lower():
            raise RuntimeError(
                f"Embedding 模型 {self.model_name} 免费额度已用完！"
                "请在阿里云百炼控制台确认免费额度，或关闭「仅使用免费额度」模式。"
            )

        raise RuntimeError(f"Embedding API 错误: {resp.status_code} - {error_msg}")

    @staticmethod
    def _is_quota_error(error: Exception) -> bool:
        msg = str(error).lower()
        return "免费额度已用完" in str(error) or "exhausted" in msg
