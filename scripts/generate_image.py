import os
import base64
import re
import json
import time
import shutil
import logging
import requests
from datetime import datetime

# ========================
# 日志配置
# ========================

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
log_file = os.path.join(LOG_DIR, f"{datetime.now().strftime('%Y%m')}.log")
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ========================
# 配置
# ========================

API_KEY = os.environ.get("TUZI_API_KEY", "")  # 建议用环境变量 TUZI_API_KEY
BASE_URL = "https://api.tu-zi.com/v1"
MODEL = os.environ.get("MODEL", "nano-banana")  # 从环境变量读取，默认 nano-banana

# 生成图片的命名（从环境变量 OUTPUT_NAME 读取，或通过参数传入）
OUTPUT_NAME = os.environ.get("OUTPUT_NAME", "")

# 参考图片路径（从环境变量 IMAGE_PATHS 读取，多个用 | 分隔）
# 例如：IMAGE_PATHS=ref1.jpg|ref2.jpg
IMAGE_PATHS_STR = os.environ.get("IMAGE_PATHS", "")
IMAGE_PATHS = IMAGE_PATHS_STR.split("|") if IMAGE_PATHS_STR else []

# 生成图片的提示词（用户选择后填入，或通过环境变量 PROMPT 传入）
PROMPT = os.environ.get("PROMPT", "")

MAX_RETRY = 2
TIMEOUT = 7200

# ========================
# 飞书配置（从环境变量获取）
# ========================

# 飞书应用凭据（从环境变量读取）
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")

# 目标会话ID（从环境变量读取，可以是 chat_id 或 user_id）
# 例如：FEISHU_TARGET=oc_xxx (群聊) 或 FEISHU_TARGET=ou_xxx (个人)
FEISHU_TARGET = os.environ.get("FEISHU_TARGET", "")

# 缓存 token
_cached_token = None
_token_expires_at = 0


def get_feishu_token():
    """获取飞书 tenant_access_token"""
    global _cached_token, _token_expires_at

    now = time.time()
    if _cached_token and now < _token_expires_at - 300:  # 提前5分钟过期
        return _cached_token

    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    data = {
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
    }

    r = requests.post(url, json=data, timeout=30)
    result = r.json()

    if result.get("code") == 0:
        _cached_token = result["tenant_access_token"]
        _token_expires_at = now + result["expire"]
        logger.info("获取飞书 token 成功")
        return _cached_token
    else:
        raise Exception(f"获取飞书 token 失败: {result}")


def upload_image_to_feishu(image_path):
    """上传图片到飞书，返回 image_key"""
    token = get_feishu_token()

    url = "https://open.feishu.cn/open-apis/im/v1/images"
    headers = {"Authorization": f"Bearer {token}"}

    # 使用 multipart/form-data 上传
    with open(image_path, "rb") as f:
        files = {
            "image_type": (None, "message"),
            "image": (os.path.basename(image_path), f, "image/png")
        }

        r = requests.post(url, headers=headers, files=files, timeout=300)
        result = r.json()

    if result.get("code") == 0:
        image_key = result["data"]["image_key"]
        logger.info(f"上传图片到飞书成功: {image_key}")
        return image_key
    else:
        raise Exception(f"上传图片到飞书失败: {result}")


def send_image_to_feishu(image_key, target=None):
    """发送图片消息到飞书群聊或个人"""
    token = get_feishu_token()

    target_id = target or FEISHU_TARGET

    if not target_id:
        raise Exception("未设置目标会话ID，请通过 FEISHU_TARGET 环境变量传入")

    # 判断是群聊还是个人
    if target_id.startswith("oc_"):
        receive_id_type = "chat_id"
    else:
        receive_id_type = "open_id"

    url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    data = {
        "receive_id": target_id,
        "msg_type": "image",
        "content": json.dumps({"image_key": image_key})
    }

    r = requests.post(url, headers=headers, json=data, timeout=30)
    result = r.json()

    if result.get("code") == 0:
        msg_id = result["data"]["message_id"]
        logger.info(f"发送图片到飞书成功: {msg_id}")
        return msg_id
    else:
        raise Exception(f"发送图片到飞书失败: {result}")

# ========================
# 常量
# ========================

# 生成的图片存放在 gen/，参考图片从 reference_images/ 读取
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REFERENCE_DIR = os.path.join(SCRIPT_DIR, "reference_images")  # 参考图目录
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "gen")  # 输出目录

# 确保目录存在
os.makedirs(REFERENCE_DIR, exist_ok=True)

# ========================
# 工具函数
# ========================

def create_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return OUTPUT_DIR


def get_output_path(filename):
    """获取生成图片的完整路径（输出到 gen/）"""
    return os.path.join(OUTPUT_DIR, filename)


def image_to_base64(path):
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def get_full_path(filename):
    """获取参考图片的完整路径（从 reference_images/ 读取）"""
    if os.path.isabs(filename):
        return filename
    return os.path.join(REFERENCE_DIR, filename)


def build_messages():
    content = [{"type": "text", "text": PROMPT}]

    for img in IMAGE_PATHS:
        full_path = get_full_path(img)
        content.append({
            "type": "image_url",
            "image_url": {"url": image_to_base64(full_path)}
        })

    return [{"role": "user", "content": content}]


# ========================
# API调用
# ========================

def call_api(messages):

    url = f"{BASE_URL}/chat/completions"

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": MODEL,
        "messages": messages
    }

    # 记录原始请求（API_KEY 脱敏）
    safe_headers = {k: (v[:20] + "..." if k == "Authorization" else v) for k, v in headers.items()}
    logger.info(f"请求 → {url}")
    logger.info(f"请求头 → {safe_headers}")
    logger.info(f"请求体 → {json.dumps(payload, ensure_ascii=False)[:500]}...")

    for i in range(MAX_RETRY):

        try:

            r = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=TIMEOUT,
                proxies={"http": None, "https": None}
            )

            r.raise_for_status()

            # 记录响应
            logger.info(f"响应状态 → {r.status_code}")
            logger.info(f"响应体 → {r.text[:500]}...")

            return r.json()

        except Exception as e:

            logger.error(f"请求失败: {e}")

            if i == MAX_RETRY - 1:
                raise

            sleep = 2 ** i
            logger.info(f"重试 {sleep}s...")
            time.sleep(sleep)


# ========================
# 内容解析
# ========================

def save_images_from_content(content, output_dir):

    img_index = 1
    saved_files = []

    # base64
    base64_pattern = r"data:image/[^;]+;base64,([A-Za-z0-9+/=\n\r]+)"

    for m in re.finditer(base64_pattern, content):

        data = base64.b64decode(m.group(1))

        # 使用 OUTPUT_NAME 或默认命名
        if OUTPUT_NAME:
            ext = ".png"  # base64 默认 png
            filename = f"{OUTPUT_NAME}{ext}"
        else:
            filename = f"image_{img_index}.png"

        path = os.path.join(output_dir, filename)

        with open(path, "wb") as f:
            f.write(data)

        saved_files.append(path)

        content = content.replace(m.group(0), f"[{filename}]")

        img_index += 1

    # url
    url_pattern = r"https?://[^\s<>\"']+\.(png|jpg|jpeg|gif)"

    for m in re.finditer(url_pattern, content):

        url = m.group(0)

        try:

            r = requests.get(url)

            # 使用 OUTPUT_NAME 或默认命名
            if OUTPUT_NAME:
                ext = os.path.splitext(url)[1]
                filename = f"{OUTPUT_NAME}{ext}"
            else:
                filename = f"url_{img_index}.png"

            path = os.path.join(output_dir, filename)

            with open(path, "wb") as f:
                f.write(r.content)

            saved_files.append(path)
            logger.info(f"下载成功: {filename}")

            content = content.replace(url, f"[{filename}]")

            img_index += 1

        except Exception as e:
            logger.error(f"下载图片失败: {e}")

    return content, saved_files


# ========================
# 主流程
# ========================

def main():

    if not API_KEY:
        raise ValueError("请设置 API_KEY 环境变量")

    if not PROMPT:
        raise ValueError("请设置 PROMPT")

    # 飞书配置验证
    if not FEISHU_APP_ID:
        raise ValueError("请设置 FEISHU_APP_ID 环境变量")
    if not FEISHU_APP_SECRET:
        raise ValueError("请设置 FEISHU_APP_SECRET 环境变量")
    if not FEISHU_TARGET:
        raise ValueError("请设置 FEISHU_TARGET 环境变量（chat_id 或 open_id）")

    # 记录请求日志
    logger.info(f"请求 | PROMPT: {PROMPT[:100]}... | 图片: {len(IMAGE_PATHS)}张 | 输出: {OUTPUT_NAME or '默认'}")

    output_dir = create_output_dir()

    messages = build_messages()

    response = call_api(messages)

    content = response["choices"][0]["message"]["content"]

    content, saved_files = save_images_from_content(content, output_dir)

    # 文件已直接保存到 output_dir (gen/)，只需提取文件名
    generated_filenames = []
    for i, src in enumerate(saved_files):
        if OUTPUT_NAME:
            ext = os.path.splitext(src)[1]
            filename = f"{OUTPUT_NAME}{ext}" if i == 0 else f"{OUTPUT_NAME}_{i+1}{ext}"
            # 重命名文件
            dst = os.path.join(output_dir, filename)
            if src != dst:
                os.rename(src, dst)
        else:
            filename = os.path.basename(src)
        generated_filenames.append(filename)
        logger.info(f"已保存: {filename}")

    logger.info(f"完成 | 输出: {', '.join(generated_filenames)}")

    # 发送到飞书
    if generated_filenames:
        try:
            latest_filename = generated_filenames[0]  # 第一张生成的图片
            latest_path = os.path.join(output_dir, latest_filename)
            logger.info(f"正在上传图片到飞书...")
            image_key = upload_image_to_feishu(latest_path)
            msg_id = send_image_to_feishu(image_key)
            logger.info(f"已发送到飞书: {latest_filename} (msg_id: {msg_id})")
            # 输出文件名供调用者获取
            print(f"OUTPUT_FILENAME: {latest_filename}")
        except Exception as e:
            logger.error(f"发送到飞书失败: {e}")


if __name__ == "__main__":
    main()
