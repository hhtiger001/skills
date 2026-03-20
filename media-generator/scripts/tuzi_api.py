"""
兔子 API 统一封装工具

支持 4 种接口格式，根据模型名自动路由：
1. gemini  — POST /v1beta/models/{model}:generateContent
2. images  — POST /v1/images/generations
3. chat    — POST /v1/chat/completions
4. videos  — POST /v1/videos (multipart/form-data)

依赖：requests
"""

from __future__ import annotations

import base64
import mimetypes
import os
import re
import time
import urllib.request
from typing import Dict, List, Optional, Union

import requests


# ---------------------------------------------------------------------------
# 图片输入归一化
# ---------------------------------------------------------------------------

def _load_image_as_base64(src: str) -> tuple[str, str]:
    """将图片源（本地路径 / URL / base64 字符串）统一为 (base64_data, mime_type)。

    - 本地文件路径 → 读取并 base64 编码
    - HTTP(S) URL  → 下载并 base64 编码
    - 纯 base64 字符串（不含 data: 前缀）→ 直接使用，猜测 mime
    - data:image/xxx;base64,…. 格式 → 直接使用

    Returns:
        (base64_data, mime_type)  e.g. ("iVBOR...", "image/png")
    """
    if src.startswith("data:"):
        # data:image/png;base64,xxxxx
        match = re.match(r"data:([a-zA-Z0-9/+.-]+);base64,(.+)", src)
        if match:
            return match.group(2), match.group(1)
        raise ValueError(f"无法解析 data URI: {src[:60]}...")

    if re.match(r"https?://", src):
        # URL — 下载
        resp = urllib.request.urlopen(src, timeout=30)
        data = resp.read()
        ct = resp.headers.get("Content-Type", "")
        mime = ct.split(";")[0].strip() if ct else mimetypes.guess_type(src)[0] or "image/jpeg"
        return base64.b64encode(data).decode(), mime

    if os.path.isfile(src):
        # 本地文件
        with open(src, "rb") as f:
            data = f.read()
        mime = mimetypes.guess_type(src)[0] or "image/jpeg"
        return base64.b64encode(data).decode(), mime

    # 当作纯 base64 字符串
    # 尝试解码验证
    try:
        base64.b64decode(src, validate=True)
    except Exception:
        raise ValueError(f"图片参数既不是有效路径/URL，也不是合法 base64: {src[:60]}...")
    return src, "image/jpeg"


def _prepare_images(images: Optional[Union[str, List[str]]]) -> Optional[List[tuple[str, str]]]:
    """将 images 参数统一为 [(base64, mime), ...] 列表。"""
    if images is None:
        return None
    if isinstance(images, str):
        images = [images]
    return [_load_image_as_base64(img) for img in images]


def _make_image_field(name: str, source):
    """创建 multipart 图片字段。

    source 可以是：
    - str: 本地文件路径 → 读取文件
    - tuple: (base64_str, mime_type) → 解码 base64

    Returns:
        (filename, data_or_file_obj, mime_type)
    """
    if isinstance(source, str):
        if os.path.isfile(source):
            mime = mimetypes.guess_type(source)[0] or "image/jpeg"
            return (os.path.basename(source), open(source, "rb"), mime)
        # 尝试当作 base64 字符串
        try:
            base64.b64decode(source, validate=True)
            return (f"{name}.jpg", base64.b64decode(source), "image/jpeg")
        except Exception:
            raise ValueError(f"图片参数不是有效路径或 base64: {source[:60]}...")
    elif isinstance(source, tuple) and len(source) == 2:
        b64, mime = source
        return (f"{name}.jpg", base64.b64decode(b64), mime)
    else:
        raise ValueError(f"不支持的图片格式: {type(source)}")


# ---------------------------------------------------------------------------
# 模型 → 接口路由
# ---------------------------------------------------------------------------

def _route(model: str) -> str:
    """根据模型名返回接口类型: gemini / images / chat / videos。"""
    m = model.strip().lower()

    # gemini 系列和 nano-banana
    if m.startswith("gemini-") or m.startswith("nano-banana"):
        return "gemini"

    # chat 格式（同步 GPT 图像模型，不含 async 后缀）
    if m in ("gpt-image-1.5", "gpt-4o-image", "gpt-image-1.5-chat") or (
        (m.startswith("gpt-image-") or m.startswith("gpt-4o-image"))
        and not m.endswith("-async")
    ):
        # 注意：gpt-image-* 中 gpt-image-1.5 / gpt-image-1.5-chat / gpt-4o-image 走 chat
        if m in ("gpt-image-1.5", "gpt-4o-image", "gpt-image-1.5-chat"):
            return "chat"

    # videos 格式（视频模型和某些异步图片模型）
    video_prefixes = ("sora-", "veo", "doubao-seedance", "doubao-seedream", "mj_")
    # kling_video 系列走 videos，但 kling_image 不走
    if any(m.startswith(p) for p in video_prefixes) or m.startswith("kling_video") or m.startswith("kling-video"):
        return "videos"

    # 默认 images 格式
    return "images"


# ---------------------------------------------------------------------------
# size → aspectRatio / size 映射
# ---------------------------------------------------------------------------

_SIZE_TO_ASPECT: Dict[str, str] = {
    "1024x1024": "1:1",
    "1024x1792": "9:16",
    "1792x1024": "16:9",
    "1280x720": "16:9",
    "720x1280": "9:16",
    "1920x1080": "16:9",
    "1080x1920": "9:16",
}


def _size_to_aspect(size: Optional[str]) -> Optional[str]:
    """将 'WxH' 尺寸转为宽高比字符串，用于 gemini imageConfig。"""
    if not size:
        return None
    return _SIZE_TO_ASPECT.get(size)


# ---------------------------------------------------------------------------
# 接口实现
# ---------------------------------------------------------------------------

def _strip_v1(base_url: str) -> str:
    """移除 base_url 末尾的 /v1（Gemini 原生 API 路径自带 /v1beta 前缀，避免拼接出 /v1/v1beta）。"""
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base


def _generate_gemini(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    images: Optional[List[tuple[str, str]]],
    size: Optional[str],
    **_kwargs,
) -> dict:
    """Gemini generateContent 接口。"""
    url = f"{_strip_v1(base_url)}/v1beta/models/{model}:generateContent"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    parts: list = [{"text": prompt}]
    if images:
        for b64, mime in images:
            parts.append({"inline_data": {"mime_type": mime, "data": b64}})

    body: dict = {"contents": [{"parts": parts}]}

    gen_config: dict = {"responseModalities": ["IMAGE"]}
    aspect = _size_to_aspect(size)
    if aspect:
        gen_config["imageConfig"] = {"aspectRatio": aspect}
    body["generationConfig"] = gen_config

    resp = requests.post(url, headers=headers, json=body, timeout=1200)
    resp.raise_for_status()
    data = resp.json()

    # 提取图片（优先 inlineData，其次 text 中的 markdown/裸 URL）
    try:
        candidates = data["candidates"]
        parts_out = candidates[0]["content"]["parts"]
        for part in parts_out:
            if "inlineData" in part:
                b64_out = part["inlineData"]["data"]
                mime_out = part["inlineData"].get("mime_type", "image/png")
                return {
                    "status": "completed",
                    "url": f"data:{mime_out};base64,{b64_out}",
                    "format": "image",
                }
            if "text" in part:
                text = part["text"]
                # markdown 图片链接: ![...](url)
                md_match = re.search(r"!\[.*?\]\((https?://[^)]+)\)", text)
                if md_match:
                    return {"status": "completed", "url": md_match.group(1), "format": "image"}
                # 裸 URL（图片扩展名）
                url_match = re.search(
                    r"(https?://[^\s<>\"')\]]+\.(?:png|jpg|jpeg|gif|webp)[^\s<>\"')\]]*)",
                    text, re.IGNORECASE,
                )
                if url_match:
                    return {"status": "completed", "url": url_match.group(1), "format": "image"}
    except (KeyError, IndexError) as exc:
        return {
            "status": "failed",
            "error": f"解析 Gemini 响应失败: {exc}",
            "format": "image",
        }

    return {"status": "failed", "error": "Gemini 响应中未找到图片", "format": "image"}


def _generate_images(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    images: Optional[List[tuple[str, str]]],
    size: Optional[str],
    **_kwargs,
) -> dict:
    """Images generations 接口。"""
    url = f"{base_url.rstrip('/')}/v1/images/generations"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    body: dict = {
        "model": model,
        "prompt": prompt,
        "response_format": "url",
    }
    if size:
        body["size"] = size

    # images 接口只支持单张参考图（取第一张）
    if images:
        b64, _mime = images[0]
        # 优先用 data URI
        body["image"] = f"data:image/jpeg;base64,{b64}"

    resp = requests.post(url, headers=headers, json=body, timeout=1200)
    resp.raise_for_status()
    data = resp.json()

    try:
        img_url = data["data"][0]["url"]
        return {
            "status": "completed",
            "url": img_url,
            "format": "image",
        }
    except (KeyError, IndexError) as exc:
        return {
            "status": "failed",
            "error": f"解析 Images 响应失败: {exc}",
            "format": "image",
        }


def _generate_chat(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    images: Optional[List[tuple[str, str]]],
    size: Optional[str],
    **_kwargs,
) -> dict:
    """Chat completions 接口（用于 gpt-image-1.5 等）。"""
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    content: list = [{"type": "text", "text": prompt}]
    if images:
        for b64, mime in images:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
    if size:
        content.append({"type": "text", "text": f"请生成 {size} 尺寸的图片"})

    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
    }

    resp = requests.post(url, headers=headers, json=body, timeout=1200)
    resp.raise_for_status()
    data = resp.json()

    try:
        msg_content = data["choices"][0]["message"]["content"]
        # 尝试从 markdown 图片链接中提取 URL
        if isinstance(msg_content, str):
            # 查找 ![...](url) 格式
            md_match = re.search(r"!\[.*?\]\((https?://[^)]+)\)", msg_content)
            if md_match:
                return {
                    "status": "completed",
                    "url": md_match.group(1),
                    "format": "image",
                }
            # 查找裸 URL
            url_match = re.search(r"(https?://[^\s<>\"')\]]+\.(?:png|jpg|jpeg|gif|webp)[^\s<>\"')\]]*)", msg_content, re.IGNORECASE)
            if url_match:
                return {
                    "status": "completed",
                    "url": url_match.group(1),
                    "format": "image",
                }
            # 尝试解析为 JSON（某些模型返回 JSON）
            try:
                parsed = __import__("json").loads(msg_content)
                if isinstance(parsed, dict) and "url" in parsed:
                    return {"status": "completed", "url": parsed["url"], "format": "image"}
            except Exception:
                pass
            # 没找到 URL，返回原始内容
            return {
                "status": "completed",
                "url": msg_content,
                "format": "image",
            }
    except (KeyError, IndexError) as exc:
        return {
            "status": "failed",
            "error": f"解析 Chat 响应失败: {exc}",
            "format": "image",
        }

    return {"status": "failed", "error": "Chat 响应中未找到图片", "format": "image"}


def submit_video_task(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    images,
    size: Optional[str],
    seconds,
    **kwargs,
) -> dict:
    """创建视频/异步图片任务，返回 {"task_id": "...", "status": "..."}。

    Args:
        images: 参考图列表，支持本地路径(str) 或 (base64, mime) 元组，→ input_reference 字段
        kwargs: 可包含 first_frame_image / last_frame_image（本地路径或元组）
    """
    url = f"{base_url.rstrip('/')}/v1/videos"
    headers = {"Authorization": f"Bearer {api_key}"}

    # 使用 list 支持多个同名字段
    fields: list = [
        ("model", (None, model)),
        ("prompt", (None, prompt)),
    ]
    if size:
        fields.append(("size", (None, size)))
    if seconds:
        fields.append(("seconds", (None, str(seconds))))

    opened_files: list = []

    try:
        # 参考图 → input_reference（支持多张）
        if images:
            for img in images:
                field_tuple = _make_image_field("input_reference", img)
                fields.append(("input_reference", field_tuple))
                if hasattr(field_tuple[1], "read"):
                    opened_files.append(field_tuple[1])

        # 首帧图 → first_frame_image
        ff = kwargs.get("first_frame_image")
        if ff:
            field_tuple = _make_image_field("first_frame_image", ff)
            fields.append(("first_frame_image", field_tuple))
            if hasattr(field_tuple[1], "read"):
                opened_files.append(field_tuple[1])

        # 尾帧图 → last_frame_image
        lf = kwargs.get("last_frame_image")
        if lf:
            field_tuple = _make_image_field("last_frame_image", lf)
            fields.append(("last_frame_image", field_tuple))
            if hasattr(field_tuple[1], "read"):
                opened_files.append(field_tuple[1])

        resp = requests.post(url, headers=headers, files=fields, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.HTTPError as exc:
        return {"status": "failed", "error": f"HTTP 错误 {exc.response.status_code}: {exc.response.text[:200]}"}
    except Exception as exc:
        return {"status": "failed", "error": f"创建任务失败: {exc}"}
    finally:
        for f in opened_files:
            f.close()

    task_id = data.get("task_id") or data.get("id")
    if not task_id:
        return {"status": "failed", "error": f"创建任务失败，响应无 task_id: {data}"}

    return {"task_id": task_id, "status": data.get("status", "unknown")}


def poll_video_task(
    base_url: str,
    api_key: str,
    task_id: str,
    timeout: int = 600,
    poll_interval: int = 5,
    progress_callback=None,
) -> dict:
    """轮询视频任务，返回 {"status": "completed", "url": "...", "task_id": "..."}。

    Args:
        progress_callback: 可选回调 progress_callback(status, progress)
    """
    poll_url = f"{base_url.rstrip('/')}/v1/videos/{task_id}"
    poll_headers = {"Authorization": f"Bearer {api_key}"}
    deadline = time.time() + timeout

    while time.time() < deadline:
        time.sleep(poll_interval)
        try:
            poll_resp = requests.get(poll_url, headers=poll_headers, timeout=30)
            poll_resp.raise_for_status()
            poll_data = poll_resp.json()
        except Exception:
            continue

        status = poll_data.get("status", "").lower()
        progress = poll_data.get("progress", 0)

        if progress_callback:
            progress_callback(status, progress)

        if status in ("completed", "succeeded", "success"):
            video_url = poll_data.get("video_url") or poll_data.get("url", "")
            if video_url:
                return {"status": "completed", "url": video_url, "task_id": task_id}
            return {"status": "failed", "error": f"任务完成但无 URL: {poll_data}", "task_id": task_id}

        if status in ("failed", "error", "cancelled"):
            return {"status": "failed", "error": f"任务失败: {poll_data.get('error', poll_data)}", "task_id": task_id}

    return {"status": "failed", "error": f"任务超时（{timeout}s）", "task_id": task_id}


def _generate_videos(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    images: Optional[List[tuple[str, str]]],
    size: Optional[str],
    seconds: Optional[int] = None,
    timeout: int = 600,
    poll_interval: int = 5,
    **_kwargs,
) -> dict:
    """Videos 接口（创建任务 + 轮询）。"""
    # _generate_videos 的 images: [(base64, mime), ...]
    # images[0] → first_frame_image, images[1] → last_frame_image
    submit_kwargs: dict = {}
    if images and len(images) >= 1:
        submit_kwargs["first_frame_image"] = images[0]
    if images and len(images) >= 2:
        submit_kwargs["last_frame_image"] = images[1]

    result = submit_video_task(
        base_url=base_url,
        api_key=api_key,
        model=model,
        prompt=prompt,
        images=None,
        size=size,
        seconds=seconds,
        **submit_kwargs,
    )

    if result["status"] == "failed":
        return {**result, "format": "video"}

    poll_result = poll_video_task(
        base_url=base_url,
        api_key=api_key,
        task_id=result["task_id"],
        timeout=timeout,
        poll_interval=poll_interval,
    )

    return {**poll_result, "format": "video"}


# ---------------------------------------------------------------------------
# 分发映射
# ---------------------------------------------------------------------------

_DISPATCH = {
    "gemini": _generate_gemini,
    "images": _generate_images,
    "chat": _generate_chat,
    "videos": _generate_videos,
}


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

def generate(
    model: str,
    prompt: str,
    images: Optional[Union[str, List[str]]] = None,
    size: Optional[str] = None,
    seconds: Optional[int] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: int = 600,
    poll_interval: int = 5,
) -> dict:
    """调用兔子 API 生成图片或视频。

    Args:
        model: 模型名，如 "seedream-3.0", "gpt-image-1.5", "sora-1.0" 等。
        prompt: 生成提示词。
        images: 参考图片，支持本地路径、URL、base64 字符串，或上述类型的列表。
        size: 图片尺寸，如 "1024x1024"。
        seconds: 视频时长（秒），仅视频模型有效。
        base_url: API 基础 URL。默认从环境变量 TUZI_BASE_URL 读取。
        api_key: API 密钥。默认从环境变量 TUZI_API_KEY 读取。
        timeout: 超时时间（秒），默认 600。
        poll_interval: 视频轮询间隔（秒），默认 5。

    Returns:
        dict: {
            "status": "completed" | "failed",
            "url": "生成结果的 URL 或 data URI",
            "format": "image" | "video",
            "task_id": "任务 ID（仅视频）",
            "error": "错误信息（仅失败时）"
        }
    """
    # 读取默认配置
    base_url = base_url or os.environ.get("TUZI_BASE_URL", "").rstrip("/")
    api_key = api_key or os.environ.get("TUZI_API_KEY", "")

    if not base_url:
        return {"status": "failed", "error": "未提供 base_url，也未设置 TUZI_BASE_URL 环境变量", "format": "unknown"}
    if not api_key:
        return {"status": "failed", "error": "未提供 api_key，也未设置 TUZI_API_KEY 环境变量", "format": "unknown"}

    # 预处理图片
    prepared_images = _prepare_images(images)

    # 路由到对应接口
    interface = _route(model)
    handler = _DISPATCH.get(interface)
    if not handler:
        return {"status": "failed", "error": f"未知接口类型: {interface}", "format": "unknown"}

    try:
        result = handler(
            base_url=base_url,
            api_key=api_key,
            model=model,
            prompt=prompt,
            images=prepared_images,
            size=size,
            seconds=seconds,
            timeout=timeout,
            poll_interval=poll_interval,
        )
        return result
    except requests.exceptions.Timeout:
        return {"status": "failed", "error": "请求超时", "format": "unknown"}
    except requests.exceptions.ConnectionError as exc:
        return {"status": "failed", "error": f"连接失败: {exc}", "format": "unknown"}
    except requests.exceptions.HTTPError as exc:
        return {"status": "failed", "error": f"HTTP 错误 {exc.response.status_code}: {exc.response.text[:200]}", "format": "unknown"}
    except Exception as exc:
        return {"status": "failed", "error": f"未知错误: {type(exc).__name__}: {exc}", "format": "unknown"}


# ---------------------------------------------------------------------------
# 面向对象封装
# ---------------------------------------------------------------------------

class TuziAPI:
    """兔子 API 的面向对象封装。

    Usage::

        api = TuziAPI(base_url="https://api.tu-zi.com", api_key="sk-xxx")
        result = api.generate("seedream-3.0", "一只可爱的猫咪", size="1024x1024")
        print(result["url"])
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: int = 600,
        poll_interval: int = 5,
    ):
        self.base_url = base_url or os.environ.get("TUZI_BASE_URL", "").rstrip("/")
        self.api_key = api_key or os.environ.get("TUZI_API_KEY", "")
        self.timeout = timeout
        self.poll_interval = poll_interval

    def generate(
        self,
        model: str,
        prompt: str,
        images: Optional[Union[str, List[str]]] = None,
        size: Optional[str] = None,
        seconds: Optional[int] = None,
    ) -> dict:
        """生成图片或视频，参数同顶层 generate() 函数。"""
        return generate(
            model=model,
            prompt=prompt,
            images=images,
            size=size,
            seconds=seconds,
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout,
            poll_interval=self.poll_interval,
        )

    def route(self, model: str) -> str:
        """查看模型会路由到哪个接口。"""
        return _route(model)

    def submit_video(
        self,
        model: str,
        prompt: str,
        images=None,
        size: Optional[str] = None,
        seconds: Optional[int] = None,
        **kwargs,
    ) -> dict:
        """提交视频/异步图片任务，返回 {"task_id": ..., "status": ...}。

        Args:
            images: 参考图列表（本地路径或 base64 元组）
            kwargs: 可包含 first_frame_image / last_frame_image
        """
        return submit_video_task(
            base_url=self.base_url,
            api_key=self.api_key,
            model=model,
            prompt=prompt,
            images=images,
            size=size,
            seconds=seconds,
            **kwargs,
        )

    def poll_video(
        self,
        task_id: str,
        timeout: Optional[int] = None,
        poll_interval: Optional[int] = None,
        progress_callback=None,
    ) -> dict:
        """轮询视频任务，返回 {"status": "completed", "url": ..., "task_id": ...}。"""
        return poll_video_task(
            base_url=self.base_url,
            api_key=self.api_key,
            task_id=task_id,
            timeout=timeout or self.timeout,
            poll_interval=poll_interval or self.poll_interval,
            progress_callback=progress_callback,
        )
