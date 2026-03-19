#!/usr/bin/env python3
"""
媒体生成脚本（图片 + 视频）
支持两种模式：
  sync  - 同步生成（chat/completions）
  async - 异步生成（/v1/videos，任务队列）

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

def image_to_base64(path):
    ext = os.path.splitext(path)[1].lower()
    mime = "image/jpeg" if ext in [".jpg", ".jpeg"] else "image/png"
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode()

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
# 同步模式（chat/completions）
# ========================

def build_messages():
    content = [{"type": "text", "text": PROMPT}]
    for img in IMAGE_PATHS:
        full_path = get_reference_path(img)
        if os.path.exists(full_path):
            content.append({"type": "image_url", "image_url": {"url": image_to_base64(full_path)}})
        else:
            logger.warning(f"参考图不存在: {full_path}")
    return [{"role": "user", "content": content}]

def call_api_sync(base_url, messages):
    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {"model": MODEL, "messages": messages}

    logger.info(f"[sync] 请求 → {url}")
    logger.info(f"[sync] 请求体 → {json.dumps(payload, ensure_ascii=False)[:500]}...")

    r = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT,
                      proxies={"http": None, "https": None})
    r.raise_for_status()
    logger.info(f"[sync] 响应状态 → {r.status_code}")
    logger.info(f"[sync] 响应体 → {r.text[:500]}...")
    return r.json()

def save_images_from_content(content):
    img_index = 1
    saved_files = []

    # base64 图片
    base64_pattern = r"data:image/[^;]+;base64,([A-Za-z0-9+/=\n\r]+)"
    for m in re.finditer(base64_pattern, content):
        data = base64.b64decode(m.group(1))
        filename = f"{OUTPUT_NAME}.png" if OUTPUT_NAME else f"image_{img_index}.png"
        path = get_output_path(filename)
        with open(path, "wb") as f:
            f.write(data)
        saved_files.append(path)
        content = content.replace(m.group(0), f"[{filename}]")
        img_index += 1

    # URL 图片
    url_pattern = r"https?://[^\s<>\"']+\.(png|jpg|jpeg|gif)"
    for m in re.finditer(url_pattern, content):
        url = m.group(0)
        try:
            ext = os.path.splitext(url)[1] or ".png"
            filename = f"{OUTPUT_NAME}{ext}" if OUTPUT_NAME else f"url_{img_index}.png"
            path = download_file(url, filename)
            saved_files.append(path)
            content = content.replace(url, f"[{filename}]")
            img_index += 1
        except Exception as e:
            logger.error(f"下载图片失败: {e}")

    return content, saved_files

def main_sync():
    base_url = get_base_url()
    media_type = detect_media_type(MODEL)
    logger.info(f"[sync] PROMPT: {PROMPT[:100]}... | 图片: {len(IMAGE_PATHS)}张 | 输出: {OUTPUT_NAME or '自动'} | 站点: {base_url}")

    messages = build_messages()
    response = call_api_sync(base_url, messages)
    content = response["choices"][0]["message"]["content"]
    content, saved_files = save_images_from_content(content)

    generated_filenames = []
    for i, src in enumerate(saved_files):
        filename = os.path.basename(src)
        if OUTPUT_NAME and i == 0:
            ext = os.path.splitext(filename)[1] or ".png"
            filename = f"{OUTPUT_NAME}{ext}"
            dst = get_output_path(filename)
            if src != dst:
                os.rename(src, dst)
        generated_filenames.append(filename)
        logger.info(f"已保存: {filename}")

    logger.info(f"[sync] 完成 | 输出: {', '.join(generated_filenames)}")
    if generated_filenames:
        print(f"OUTPUT_FILENAME: {generated_filenames[0]}")
        save_history({
            "mode": "sync", "type": media_type, "model": MODEL,
            "prompt": PROMPT, "site": base_url, "output": generated_filenames[0],
            "status": "success", "reference_images": IMAGE_PATHS,
        })

# ========================
# 异步模式（/v1/videos）
# ========================

def create_async_task(base_url):
    """创建异步图片/视频生成任务（支持多张参考图）"""
    base = base_url.replace("/v1", "") if base_url.endswith("/v1") else base_url
    url = f"{base}/v1/videos"
    headers = {"Authorization": f"Bearer {API_KEY}"}

    # 使用 list 而非 dict，支持多个同名字段（多张参考图）
    form_data = [
        ("model", (None, MODEL)),
        ("prompt", (None, PROMPT)),
    ]
    if SIZE:
        form_data.append(("size", (None, SIZE)))

    # 打开的文件句柄，用于 finally 关闭
    opened_files = []

    # 添加参考图（input_reference）— 支持多张
    for img in IMAGE_PATHS:
        full_path = get_reference_path(img)
        if os.path.exists(full_path):
            f = open(full_path, "rb")
            opened_files.append(f)
            form_data.append(("input_reference", (os.path.basename(full_path), f)))
            logger.info(f"[async] 添加参考图: {full_path}")
        else:
            logger.warning(f"[async] 参考图不存在: {full_path}")

    # 视频专用：秒数（异步模式必需，范围 4-11）
    SECONDS = os.environ.get("SECONDS", "")
    if SECONDS:
        form_data.append(("seconds", (None, SECONDS)))

    # 视频专用：首帧图
    first_frame = os.environ.get("FIRST_FRAME", "")
    if first_frame:
        ff_path = get_reference_path(first_frame)
        if os.path.exists(ff_path):
            ff_file = open(ff_path, "rb")
            opened_files.append(ff_file)
            form_data.append(("first_frame_image", (os.path.basename(ff_path), ff_file)))
            logger.info(f"[async] 添加首帧图: {ff_path}")

    # 视频专用：尾帧图
    last_frame = os.environ.get("LAST_FRAME", "")
    if last_frame:
        lf_path = get_reference_path(last_frame)
        if os.path.exists(lf_path):
            lf_file = open(lf_path, "rb")
            opened_files.append(lf_file)
            form_data.append(("last_frame_image", (os.path.basename(lf_path), lf_file)))
            logger.info(f"[async] 添加尾帧图: {lf_path}")

    logger.info(f"[async] 创建任务 → {url}")
    logger.info(f"[async] model={MODEL}, size={SIZE or '默认'}, 图片={len(IMAGE_PATHS)}张")

    try:
        r = requests.post(url, headers=headers, files=form_data, timeout=60,
                          proxies={"http": None, "https": None})
        r.raise_for_status()
    finally:
        for f in opened_files:
            f.close()

    result = r.json()
    task_id = result.get("id", "")
    status = result.get("status", "unknown")
    logger.info(f"[async] 任务已创建 | id={task_id} | status={status}")
    print(f"TASK_ID: {task_id}")
    print(f"TASK_STATUS: {status}")

    return task_id, base

def poll_async_task(task_id, base):
    """轮询查询任务状态"""
    url = f"{base}/v1/videos/{task_id}"
    headers = {"Authorization": f"Bearer {API_KEY}"}

    start_time = time.time()
    while time.time() - start_time < POLL_TIMEOUT:
        r = requests.get(url, headers=headers, timeout=30,
                         proxies={"http": None, "https": None})
        r.raise_for_status()
        result = r.json()

        status = result.get("status", "unknown")
        progress = result.get("progress", 0)
        logger.info(f"[async] 轮询 | id={task_id} | status={status} | progress={progress}%")
        print(f"TASK_STATUS: {status} | progress={progress}%")

        if status == "completed":
            return result
        elif status in ["failed", "cancelled"]:
            raise Exception(f"异步任务失败 | id={task_id} | status={status}")

        time.sleep(POLL_INTERVAL)

    raise Exception(f"异步任务超时 | id={task_id} | 等待超过 {POLL_TIMEOUT} 秒")

def extract_result_from_async(result):
    """从异步结果中提取文件 URL 并下载"""
    file_url = None

    # 按优先级尝试各字段
    for key in ["url", "output", "result"]:
        if key in result:
            val = result[key]
            if isinstance(val, str) and val.startswith("http"):
                file_url = val
                break
            elif isinstance(val, dict) and "url" in val:
                file_url = val["url"]
                break

    # 尝试 data.url
    if not file_url and "data" in result and isinstance(result["data"], dict):
        file_url = result["data"].get("url")

    # 全文搜索 URL（支持图片和视频格式）
    if not file_url:
        result_str = json.dumps(result)
        url_match = re.search(r'https?://[^\s"\'<>]+\.(png|jpg|jpeg|gif|webp|mp4)', result_str)
        if url_match:
            file_url = url_match.group(0)

    if not file_url:
        logger.error(f"[async] 无法从结果中提取 URL: {json.dumps(result)[:1000]}")
        raise Exception("异步任务完成但未找到文件 URL")

    logger.info(f"[async] 文件 URL: {file_url}")
    ext = os.path.splitext(file_url.split("?")[0])[1] or ".mp4"
    filename = f"{OUTPUT_NAME}{ext}" if OUTPUT_NAME else f"async_{int(time.time())}{ext}"
    path = download_file(file_url, filename)
    return os.path.basename(path)

def main_async():
    base_url = get_base_url()
    media_type = detect_media_type(MODEL)
    logger.info(f"[async] PROMPT: {PROMPT[:100]}... | model={MODEL} | 图片: {len(IMAGE_PATHS)}张 | 输出: {OUTPUT_NAME or '自动'} | 站点: {base_url}")

    task_id, base = create_async_task(base_url)
    logger.info(f"[async] 开始轮询任务 {task_id}...")
    result = poll_async_task(task_id, base)
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
