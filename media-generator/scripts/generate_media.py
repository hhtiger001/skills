#!/usr/bin/env python3
"""
媒体生成脚本（图片 + 视频）
支持两种模式：
  sync  - 同步生成（通过 tuzi_api 自动路由到 gemini/images/chat 接口）
  async - 异步生成（/v1/videos，任务队列，保留进度打印）

只负责生成，输出到 gen/ 目录
不发消息（发送由调用者负责）
"""
import os
import sys
import base64
import re
import json
import time
import logging
import requests
from datetime import datetime

from tuzi_api import TuziAPI

# ========================
# 日志配置
# ========================

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, f"{datetime.now().strftime('%Y%m')}.log")
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ========================
# 配置（从环境变量读取）
# ========================

API_KEY = os.environ.get("TUZI_API_KEY", "")
BASE_URL = os.environ.get("BASE_URL", "")  # 留空则自动选站
AUTO_SELECT = os.environ.get("AUTO_SELECT", "true").lower() == "true"
MODEL = os.environ.get("MODEL", "")  # 必须指定，不再写死默认值
MODE = os.environ.get("MODE", "async")  # sync | async
OUTPUT_NAME = os.environ.get("OUTPUT_NAME", "")
IMAGE_PATHS_STR = os.environ.get("IMAGE_PATHS", "")
IMAGE_PATHS = [p.strip() for p in IMAGE_PATHS_STR.split("|") if p.strip()]
PROMPT = os.environ.get("PROMPT", "")
SIZE = os.environ.get("SIZE", "")
TIMEOUT = 7200

# 异步模式轮询间隔（秒）
POLL_INTERVAL = 5
POLL_TIMEOUT = 600  # 最长等待 10 分钟（视频可能较慢）

# ========================
# 目录和文件
# ========================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REFERENCE_DIR = os.path.join(SCRIPT_DIR, "reference_images")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "gen")
MODELS_FILE = os.path.join(SCRIPT_DIR, "models.json")
HISTORY_FILE = os.path.join(OUTPUT_DIR, "history.json")

os.makedirs(REFERENCE_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ========================
# 模型列表
# ========================

def load_models():
    """从 models.json 加载模型列表"""
    try:
        with open(MODELS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"加载 models.json 失败: {e}")
        return {}

MODELS = load_models()

def get_sites():
    """获取站点列表"""
    if MODELS and "sites" in MODELS:
        return [s["url"] for s in MODELS["sites"]]
    return [
        "https://api.ourzhishi.top/v1",
        "https://apius.tu-zi.com/v1",
        "https://apicdn.tu-zi.com/v1",
        "https://api.tu-zi.com/v1",
        "https://api.sydney-ai.com/v1",
    ]

# ========================
# 历史记录
# ========================

def load_history():
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_history(record):
    history = load_history()
    record["timestamp"] = datetime.now().isoformat()
    history.append(record)
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    logger.info(f"[history] 记录已保存: {record.get('output', '?')}")

# ========================
# 站点自动选择
# ========================

def auto_select_site():
    """测试所有站点延迟，返回最快可用的站点 URL（并行检测）"""
    if not API_KEY:
        raise ValueError("请设置 TUZI_API_KEY 环境变量")

    import concurrent.futures

    logger.info("[site] 开始自动选站（并行）...")

    def test_site(site):
        try:
            start = time.time()
            r = requests.get(
                f"{site}/models",
                headers={"Authorization": f"Bearer {API_KEY}"},
                timeout=5,
                proxies={"http": None, "https": None}
            )
            latency = time.time() - start
            if r.status_code == 200:
                return (site, latency, "✅")
            else:
                return (site, latency, f"❌ HTTP {r.status_code}")
        except Exception as e:
            return (site, 0, f"❌ {type(e).__name__}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(get_sites())) as workers:
        results = list(workers.map(test_site, get_sites()))

    best_site = None
    best_latency = float('inf')
    for site, latency, status in results:
        if status == "✅" and latency < best_latency:
            best_latency = latency
            best_site = site

    print("站点检测:")
    for site, latency, status in results:
        lat_str = f"{latency:.2f}s" if latency > 0 else "-"
        print(f"  {status} {site} ({lat_str})")
        logger.info(f"[site] {status} {site} ({lat_str})")

    if best_site:
        print(f"\n🏆 自动选择: {best_site} ({best_latency:.2f}s)")
        logger.info(f"[site] 选中: {best_site} ({best_latency:.2f}s)")
        return best_site
    else:
        print("\n❌ 所有站点不可用")
        raise Exception("所有站点不可用，请稍后再试")

# ========================
# 工具函数
# ========================

def get_base_url():
    global BASE_URL
    if AUTO_SELECT and not BASE_URL:
        BASE_URL = auto_select_site()
    if not BASE_URL:
        BASE_URL = "https://api.tu-zi.com/v1"
    return BASE_URL

def get_output_path(filename):
    return os.path.join(OUTPUT_DIR, filename)

def get_reference_path(filename):
    if os.path.isabs(filename):
        return filename
    return os.path.join(REFERENCE_DIR, filename)

def download_file(url, filename):
    r = requests.get(url, timeout=300, proxies={"http": None, "https": None})
    r.raise_for_status()
    path = get_output_path(filename)
    with open(path, "wb") as f:
        f.write(r.content)
    logger.info(f"下载成功: {filename} ({len(r.content)} bytes)")
    return path

def detect_media_type(model_name):
    """根据模型名判断是图片还是视频"""
    if any(k in model_name for k in ["seedance", "sora"]):
        return "video"
    return "image"

# ========================
# 同步模式（通过 tuzi_api）
# ========================

def save_result(url, media_format):
    """保存生成结果（data URI 或 HTTP URL）到文件，返回文件名"""
    if url.startswith("data:"):
        # data URI → 解码 base64 并保存
        match = re.match(r"data:([^;]+);base64,(.+)", url)
        if not match:
            raise Exception(f"无法解析 data URI: {url[:60]}...")
        mime = match.group(1)
        ext_map = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
        ext = ext_map.get(mime, ".png")
        data = base64.b64decode(match.group(2))
        filename = f"{OUTPUT_NAME}{ext}" if OUTPUT_NAME else f"sync_{int(time.time())}{ext}"
        path = get_output_path(filename)
        with open(path, "wb") as f:
            f.write(data)
        logger.info(f"已保存: {filename} ({len(data)} bytes)")
    else:
        # HTTP URL → 下载
        ext = os.path.splitext(url.split("?")[0])[1] or ".png"
        if media_format == "video":
            ext = ".mp4"
        filename = f"{OUTPUT_NAME}{ext}" if OUTPUT_NAME else f"sync_{int(time.time())}{ext}"
        download_file(url, filename)

    return filename

def main_sync():
    base_url = get_base_url()
    api = TuziAPI(api_key=API_KEY, base_url=base_url, timeout=TIMEOUT, poll_interval=POLL_INTERVAL)

    # 构建参考图路径列表
    image_list = []
    for img in IMAGE_PATHS:
        full_path = get_reference_path(img)
        if os.path.exists(full_path):
            image_list.append(full_path)
        else:
            logger.warning(f"参考图不存在: {full_path}")

    logger.info(f"[sync] PROMPT: {PROMPT[:100]}... | 图片: {len(image_list)}张 | 输出: {OUTPUT_NAME or '自动'} | 站点: {base_url}")

    result = api.generate(
        model=MODEL,
        prompt=PROMPT,
        images=image_list or None,
        size=SIZE or None,
    )

    if result["status"] != "completed":
        error_msg = result.get("error", "未知错误")
        logger.error(f"[sync] 生成失败: {error_msg}")
        raise Exception(f"同步生成失败: {error_msg}")

    url = result["url"]
    media_format = result.get("format", "image")
    filename = save_result(url, media_format)

    logger.info(f"[sync] 完成 | 输出: {filename}")
    print(f"OUTPUT_FILENAME: {filename}")
    save_history({
        "mode": "sync", "type": media_format, "model": MODEL,
        "prompt": PROMPT, "site": base_url, "output": filename,
        "status": "success", "reference_images": IMAGE_PATHS,
    })

# ========================
# 异步模式（通过 tuzi_api 统一封装）
# ========================

def create_async_task(base_url):
    """创建异步图片/视频生成任务（通过 TuziAPI）"""
    api = TuziAPI(api_key=API_KEY, base_url=base_url)

    # 准备 images 参数（本地文件路径列表）
    img_paths = [get_reference_path(p) for p in IMAGE_PATHS if os.path.exists(get_reference_path(p))]

    # 准备 kwargs
    kwargs = {}
    if SIZE:
        kwargs["size"] = SIZE
    seconds = os.environ.get("SECONDS", "")
    if seconds:
        kwargs["seconds"] = int(seconds)

    # 首帧图
    first_frame = os.environ.get("FIRST_FRAME", "")
    if first_frame:
        ff_path = get_reference_path(first_frame)
        if os.path.exists(ff_path):
            kwargs["first_frame_image"] = ff_path
            logger.info(f"[async] 添加首帧图: {ff_path}")

    # 尾帧图
    last_frame = os.environ.get("LAST_FRAME", "")
    if last_frame:
        lf_path = get_reference_path(last_frame)
        if os.path.exists(lf_path):
            kwargs["last_frame_image"] = lf_path
            logger.info(f"[async] 添加尾帧图: {lf_path}")

    logger.info(f"[async] 创建任务 | model={MODEL}, size={SIZE or '默认'}, 图片={len(img_paths)}张")

    result = api.submit_video(MODEL, PROMPT, images=img_paths or None, **kwargs)

    task_id = result.get("task_id", "")
    logger.info(f"[async] 任务已创建 | id={task_id} | status={result.get('status', 'unknown')}")
    print(f"TASK_ID: {task_id}")
    print(f"TASK_STATUS: {result.get('status', 'unknown')}")

    return task_id, base_url


def poll_async_task(task_id, base_url):
    """轮询查询任务状态（通过 TuziAPI）"""
    api = TuziAPI(api_key=API_KEY, base_url=base_url)

    def on_progress(status, progress):
        logger.info(f"[async] 轮询 | id={task_id} | status={status} | progress={progress}%")
        print(f"TASK_STATUS: {status} | progress={progress}%")

    result = api.poll_video(
        task_id,
        timeout=POLL_TIMEOUT,
        poll_interval=POLL_INTERVAL,
        progress_callback=on_progress,
    )

    if result["status"] == "completed":
        return result
    else:
        raise Exception(f"异步任务失败 | id={task_id} | {result.get('error', '')}")


def extract_result_from_async(result):
    """从异步结果中提取文件 URL 并下载"""
    file_url = result.get("url", "")
    if not file_url:
        raise Exception(f"无文件 URL: {result}")

    logger.info(f"[async] 文件 URL: {file_url}")
    ext = os.path.splitext(file_url.split("?")[0])[1] or ".mp4"
    filename = f"{OUTPUT_NAME}{ext}" if OUTPUT_NAME else f"async_{int(time.time())}{ext}"
    path = download_file(file_url, filename)
    return os.path.basename(path)

def main_async():
    base_url = get_base_url()
    media_type = detect_media_type(MODEL)
    logger.info(f"[async] PROMPT: {PROMPT[:100]}... | model={MODEL} | 图片: {len(IMAGE_PATHS)}张 | 输出: {OUTPUT_NAME or '自动'} | 站点: {base_url}")

    task_id, base_url = create_async_task(base_url)
    logger.info(f"[async] 开始轮询任务 {task_id}...")
    result = poll_async_task(task_id, base_url)
    filename = extract_result_from_async(result)

    logger.info(f"[async] 完成 | 输出: {filename}")
    print(f"OUTPUT_FILENAME: {filename}")

    save_history({
        "mode": "async", "type": media_type, "model": MODEL,
        "prompt": PROMPT, "site": base_url, "task_id": task_id,
        "output": filename, "status": "success",
        "reference_images": IMAGE_PATHS, "size": SIZE,
    })

# ========================
# 入口
# ========================

def main():
    if not API_KEY:
        raise ValueError("请设置 TUZI_API_KEY 环境变量")
    if not PROMPT:
        raise ValueError("请设置 PROMPT 环境变量")
    if not MODEL:
        raise ValueError("请设置 MODEL 环境变量（agent 从 models.json 读取后传入）")

    if MODE == "async":
        main_async()
    else:
        main_sync()

def check_sites():
    """检查所有站点的可用性和延迟"""
    if not API_KEY:
        print("站点连通性检查（无 API Key，仅检测 HTTP 状态）:")
        for site in get_sites():
            try:
                start = time.time()
                r = requests.get(f"{site}/models", timeout=5,
                                 proxies={"http": None, "https": None})
                latency = time.time() - start
                if r.status_code == 200:
                    print(f"  ✅ {site} ({latency:.2f}s)")
                elif r.status_code == 401:
                    print(f"  ✅ {site} ({latency:.2f}s) — 在线（需 API Key）")
                else:
                    print(f"  ❌ {site} → HTTP {r.status_code}")
            except Exception as e:
                print(f"  ❌ {site} → {type(e).__name__}")
    else:
        auto_select_site()

def list_models():
    """列出所有可用模型"""
    if not MODELS:
        print("models.json 未找到或格式错误")
        return

    for category, modes in MODELS.items():
        if category in ("sites", "sizes"):
            continue
        for mode, model_list in modes.items():
            print(f"\n{'='*50}")
            print(f"  {category} ({mode})")
            print(f"{'='*50}")
            for m in model_list:
                tags = " ".join(m.get("tags", []))
                line = f"  {m['model']:<45} {m['price']:<20}"
                if tags:
                    line += f"{tags}"
                print(line)
                if m.get("note"):
                    print(f"    └─ {m['note']}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "--check":
            check_sites()
        elif cmd == "--models":
            list_models()
        else:
            main()
    else:
        main()
