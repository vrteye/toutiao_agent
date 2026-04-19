"""DashScope 图片生成封装

支持：
- qwen-image-2.0-pro：同步 MultiModalConversation 接口
- wanx 系列：旧版异步 image-synthesis 接口
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Optional

import httpx
from dashscope import MultiModalConversation
from loguru import logger

from config.settings import settings, PROJECT_ROOT


class WanxImageGenerator:
    """DashScope 文生图调用封装"""

    SUBMIT_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
    TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"

    def __init__(self, output_dir: str | Path | None = None):
        cfg = settings.models.image_gen
        self.model = cfg.name
        self.style = cfg.style
        self.size = cfg.size
        self.n = cfg.n
        self.async_mode = cfg.async_mode
        self.poll_interval = cfg.poll_interval
        self.max_poll_times = cfg.max_poll_times
        self.api_key = settings.dashscope_api_key

        self.output_dir = Path(output_dir) if output_dir else PROJECT_ROOT / "output" / "images"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, prompt: str, negative_prompt: str = "", n: int | None = None) -> list[str]:
        """
        生成卡通图片，返回本地文件路径列表

        Args:
            prompt: 图片描述 Prompt
            negative_prompt: 反向 Prompt
            n: 生成数量（默认使用配置值）

        Returns:
            本地图片文件路径列表
        """
        n = n or self.n
        logger.info(f"[图片] 开始生成 {n} 张图片, 模型: {self.model}")

        if self._is_qwen_image_model:
            return self._generate_qwen_images(prompt, negative_prompt, n)

        # Step 1: 提交任务
        task_ids = self._submit_tasks(prompt, negative_prompt, n)
        if not task_ids:
            logger.error("[图片] 任务提交失败")
            return []

        # Step 2: 并行轮询所有任务
        logger.info(f"[图片] 已提交 {len(task_ids)} 个任务，等待生成...")
        results = self._poll_all_tasks(task_ids)

        # Step 3: 下载图片
        paths = []
        for i, url in enumerate(results):
            try:
                path = self._download_image(url, index=i)
                if path:
                    paths.append(path)
            except Exception as e:
                logger.error(f"[图片] 第 {i + 1} 张下载失败: {e}")

        logger.info(f"[图片] 生成完成: {len(paths)}/{n} 张")
        return paths

    def generate_scenes(self, scenes: list[str], negative_prompt: str = "") -> list[str]:
        """为多个场景分别生成图片（每场景一张）"""
        all_paths = []
        for i, scene in enumerate(scenes):
            logger.info(f"[图片] 生成第 {i + 1}/{len(scenes)} 张: {scene[:20]}...")
            paths = self.generate(prompt=scene, negative_prompt=negative_prompt, n=1)
            if paths:
                all_paths.extend(paths)
        return all_paths

    @property
    def _is_qwen_image_model(self) -> bool:
        return self.model.startswith("qwen-image")

    def _generate_qwen_images(self, prompt: str, negative_prompt: str, n: int) -> list[str]:
        """使用 qwen-image-2.0-pro 同步接口生成图片。"""
        n = max(1, min(n, 6))
        prompt = self._build_qwen_prompt(prompt)
        negative_prompt = negative_prompt or "模糊, 低质量, 变形, 文字水印"

        messages = [
            {
                "role": "user",
                "content": [{"text": prompt[:800]}],
            }
        ]

        try:
            response = MultiModalConversation.call(
                api_key=self.api_key,
                model=self.model,
                messages=messages,
                result_format="message",
                stream=False,
                watermark=False,
                prompt_extend=True,
                negative_prompt=negative_prompt[:500],
                size=self.size,
                n=n,
            )
        except Exception as e:
            logger.error(f"[图片] qwen-image 调用异常: {e}")
            return []

        if response.status_code != 200:
            logger.error(
                "[图片] qwen-image 调用失败: "
                f"{response.status_code} - {response.code} - {response.message}"
            )
            return []

        urls = self._extract_qwen_image_urls(response.output)
        if not urls:
            logger.error(f"[图片] qwen-image 未返回图片 URL: {response.output}")
            return []

        paths = []
        for i, url in enumerate(urls[:n]):
            path = self._download_image(url, index=i)
            if path:
                paths.append(path)

        logger.info(f"[图片] qwen-image 生成完成: {len(paths)}/{n} 张")
        return paths

    def _build_qwen_prompt(self, prompt: str) -> str:
        """qwen-image 不支持 style 参数，风格放进提示词。"""
        if self.style and self.style not in prompt:
            return f"{self.style}风格, {prompt}"
        return prompt

    @staticmethod
    def _extract_qwen_image_urls(output) -> list[str]:
        urls = []
        for choice in WanxImageGenerator._value(output, "choices", []):
            message = WanxImageGenerator._value(choice, "message", {})
            for item in WanxImageGenerator._value(message, "content", []):
                url = WanxImageGenerator._value(item, "image", "")
                if url:
                    urls.append(url)
        return urls

    @staticmethod
    def _value(obj, key: str, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _submit_tasks(self, prompt: str, negative_prompt: str, n: int) -> list[str]:
        """提交异步任务，返回 task_id 列表"""
        task_ids = []

        for _ in range(n):
            payload = {
                "model": self.model,
                "input": {
                    "prompt": prompt,
                    "negative_prompt": negative_prompt or "模糊, 低质量, 变形, 文字水印",
                },
                "parameters": {
                    "size": self.size,
                    "n": 1,
                    "style": f"<{self.style}>",
                },
            }

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "X-DashScope-Async": "enable",  # 异步模式
            }

            try:
                with httpx.Client(timeout=30) as client:
                    resp = client.post(self.SUBMIT_URL, json=payload, headers=headers)
                    data = resp.json()

                    if resp.status_code == 200 and data.get("output", {}).get("task_id"):
                        task_id = data["output"]["task_id"]
                        task_ids.append(task_id)
                        logger.debug(f"[图片] 任务已提交: {task_id}")
                    else:
                        error_msg = data.get("message", "未知错误")
                        logger.error(f"[图片] 任务提交失败: {error_msg}")
            except Exception as e:
                logger.error(f"[图片] 任务提交异常: {e}")

            time.sleep(0.5)  # 避免瞬间并发太多

        return task_ids

    def _poll_all_tasks(self, task_ids: list[str]) -> list[str]:
        """轮询所有任务直到完成，返回图片 URL 列表"""
        results = [None] * len(task_ids)
        pending = list(range(len(task_ids)))

        for poll in range(self.max_poll_times):
            if not pending:
                break

            new_pending = []
            for idx in pending:
                task_id = task_ids[idx]
                status, url = self._check_task(task_id)
                if status == "SUCCEEDED" and url:
                    results[idx] = url
                    logger.info(f"[图片] 任务 {task_id[:8]}... 完成")
                elif status == "FAILED":
                    logger.error(f"[图片] 任务 {task_id[:8]}... 失败")
                else:
                    new_pending.append(idx)

            pending = new_pending
            if pending:
                time.sleep(self.poll_interval)

        if pending:
            for idx in pending:
                logger.warning(f"[图片] 任务 {task_ids[idx][:8]}... 超时")

        return [r for r in results if r]

    def _check_task(self, task_id: str) -> tuple[str, Optional[str]]:
        """查询单个任务状态，返回 (status, image_url)"""
        url = self.TASK_URL.format(task_id=task_id)
        headers = {"Authorization": f"Bearer {self.api_key}"}

        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(url, headers=headers)
                data = resp.json()

                output = data.get("output", {})
                status = output.get("task_status", output.get("status", "UNKNOWN"))

                if status == "SUCCEEDED":
                    results = output.get("results", [])
                    if results:
                        image_url = results[0].get("url", "")
                        return status, image_url

                return status, None
        except Exception as e:
            logger.debug(f"[图片] 查询任务异常: {e}")
            return "UNKNOWN", None

    def _download_image(self, url: str, index: int = 0) -> Optional[str]:
        """下载图片到本地"""
        filename = f"{uuid.uuid4().hex[:8]}_{index}.png"
        filepath = self.output_dir / filename

        try:
            with httpx.Client(timeout=60, follow_redirects=True) as client:
                resp = client.get(url)
                resp.raise_for_status()
                with open(filepath, "wb") as f:
                    f.write(resp.content)
            logger.info(f"[图片] 已保存: {filepath}")
            return str(filepath)
        except Exception as e:
            logger.error(f"[图片] 下载失败: {e}")
            return None
