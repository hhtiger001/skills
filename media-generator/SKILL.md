---
name: media-generator
description: 使用 tu-zi.com 生成图片和视频；支持下载飞书图片到参考目录、确认提示词后生成、上传并发送到飞书聊天。
---

# Media Generator

使用 tu-zi.com 的 API 生成图片和视频，支持参考图。

## 基础路径

`{baseDir}` = `~/.openclaw/skills/media-generator/scripts`

## 目录结构

```
{baseDir}/
├── gen/                      # 生成的文件（输出）
│   └── history.json          # 生成历史记录
├── logs/                     # 请求日志（按月：YYYYMM.log）
├── reference_images/         # 参考图（输入，从飞书下载）
├── .tuzi_api_key             # 缓存的 tu-zi API Key
├── models.json               # 模型/站点/尺寸配置（唯一数据源）
└── generate_media.py         # 生成脚本（图片 + 视频）
```

## 模型与站点

所有模型、站点、尺寸数据均在 `models.json` 中维护，价格变动只改此文件。

查看可用模型：
```bash
cd {baseDir} && python3 generate_media.py --models
```

**推荐模型（从 models.json 的 tags 动态查找）：**
- ⭐ **最便宜**：读取 `models.json`，找 image/video 各分类中 tags 包含 `⭐` 的模型
- 💰 **性价比**：读取 `models.json`，找 image/video 各分类中 tags 包含 `💰` 的模型

> agent 在确认提示词时，应根据用户要生成的类型（图片/视频），读取 models.json 找到对应分类，格式化展示给用户选择。推荐时标注 ⭐ 和 💰。

---

## 零、环境检查（首次使用）

Skill 依赖以下工具，首次使用时逐项检查，缺则安装：

### 依赖清单

| 依赖 | 用途 | 检查命令 | 安装命令 |
|------|------|---------|---------|
| mcporter | 调用 MCP | `mcporter --version` | `npm install -g mcporter` |
| npx | 启动 stdio 类型 MCP | `npx --version` | 随 Node.js 安装 |
| uvx | 启动 Python 类型 MCP | `uvx --version` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| python3 + requests | 生成脚本 | `python3 -c "import requests"` | `pip3 install requests` |

### mcporter 配置检查

```bash
# 检查是否有配置文件
cat /Users/hh/.mcporter/mcporter.json 2>/dev/null || echo "未配置"

# 查看已配置的 server
mcporter list
```

**如果没有任何 server 配置：**
告知用户需要提供 MCP 配置，让用户以 JSON 格式给出，写入 `/Users/hh/.mcporter/mcporter.json`。

配置文件格式：
```json
{
  "mcpServers": {
    "server-name": {
      "command": "npx -y @package/name",
      "env": { "API_KEY": "xxx" }
    },
    "http-server": {
      "type": "http",
      "url": "https://api.example.com/mcp",
      "headers": { "Authorization": "Bearer xxx" }
    }
  }
}
```

**验证：** 期望至少有一个 server 处于 healthy 状态，且支持图片识别工具。

---

## 一、凭据管理

### 飞书凭据

从 `~/.openclaw/openclaw.json` 读取当前 `account_id` 对应的凭据：

```bash
APP_ID=$(grep -A20 '"channels"' ~/.openclaw/openclaw.json | grep -A5 '"'$ACCOUNT_ID'"' | grep 'appId' | sed 's/.*"appId": *"\([^"]*\)".*/\1/')
APP_SECRET=$(grep -A20 '"channels"' ~/.openclaw/openclaw.json | grep -A5 '"'$ACCOUNT_ID'"' | grep 'appSecret' | sed 's/.*"appSecret": *"\([^"]*\)".*/\1/')
```

> `ACCOUNT_ID` 从 inbound metadata 的 `account_id` 字段获取

### tu-zi API Key

缓存文件：`{baseDir}/.tuzi_api_key`
- 首次使用时请求用户提供，保存到缓存文件（chmod 600）
- 后续直接从缓存读取

---

## 二、完整工作流

### 场景 A：用户发送参考图

**步骤 1：下载图片**

agent 从消息上下文获取 `message_id` 和 `image_key`，通过飞书 API 下载：

```bash
TOKEN=$(curl -s -X POST "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal" \
  -H "Content-Type: application/json" \
  -d "{\"app_id\":\"$APP_ID\",\"app_secret\":\"$APP_SECRET\"}" | python3 -c "import json,sys; print(json.load(sys.stdin)['tenant_access_token'])")

curl -s -L -H "Authorization: Bearer $TOKEN" \
  "https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{image_key}?type=image" \
  -o {baseDir}/reference_images/tmp_{YYYYMMDD_HHmmss}.jpg
```

**步骤 1.5：验证下载结果**

```bash
file {baseDir}/reference_images/tmp_{YYYYMMDD_HHmmss}.jpg
# ✅ JPEG image data / PNG image data
# ❌ JSON data / ASCII text → 下载失败，重新获取 token 重试
```

**步骤 2：识别图片并重命名**

动态发现已配置的 MCP 中支持图片识别的工具，按优先级使用：

```bash
# 查看已配置的 MCP server 及其工具
mcporter list --output json 2>/dev/null || mcporter list
```

从输出中找到包含图片识别能力的工具（关键词：`analyze_image`、`understand_image`、`image_analysis` 等），选择第一个可用的调用。

**调用方式统一为：**
```bash
mcporter call {server}.{tool} \
  image_source="{baseDir}/reference_images/tmp_{YYYYMMDD_HHmmss}.jpg" \
  prompt="描述图片内容、风格、构图，用于生成语义文件名"
```

> **如果没有找到支持图片识别的 MCP server：**
> 告知用户需要配置，让用户提供 MCP 配置（JSON 格式），写入 `/Users/hh/.mcporter/mcporter.json` 的 `mcpServers` 中，然后重新 `mcporter list` 验证。

识别完成后，根据结果提取语义关键词重命名：
```bash
mv {baseDir}/reference_images/tmp_{YYYYMMDD_HHmmss}.jpg \
   {baseDir}/reference_images/ref_{语义描述}_{YYYYMMDD_HHmmss}.jpg
```

> 命名规则：`ref_{英文关键词}_{YYYYMMDD_HHmmss}.jpg`，关键词不超过 50 字符

**步骤 3：告知用户并确认意图**

告知用户图片已保存、分析结果，询问想基于此图生成什么。

### 场景 B：用户直接描述生成（无参考图）

跳过下载和识别，直接进入「确认提示词」环节。

---

## 三、确认提示词

生成前，**必须**向用户展示并确认：

1. **原始提示词**：根据用户意图整理
2. **优化增强版提示词**：加入细节、风格、构图等增强描述，**用中文展示**
3. **模型选择**：**必须列出所有可用模型供用户选择**，读取 `models.json`，根据生成类型（图片/视频）列出对应分类下的全部模型，标注价格和推荐标签（⭐最便宜、💰性价比），让用户自行选择或接受推荐
4. **生成参数**：输出文件名、参考图列表、尺寸比例（如有）

> **默认优先使用异步方式**（`MODE=async`）

**等用户明确回复「确认生成」后才能执行，禁止未经确认就生成。**

> ⚠️ **切换模型需重新确认**：如果用户在确认后要求更换模型（或 agent 建议换模型），必须再次展示完整的确认信息（提示词 + 新模型 + 参数），等用户再次确认后才能执行。

---

## 四、执行生成

### 站点自动选择（默认）

`AUTO_SELECT=true`（默认），生成前自动检测所有站点延迟，选择最快的。无需手动指定 `BASE_URL`。

手动覆盖：`BASE_URL=xxx AUTO_SELECT=false`

### 执行命令

**异步模式（默认）：**
```bash
cd {baseDir}
TUZI_API_KEY=$(cat {baseDir}/.tuzi_api_key) \
MODE=async \
MODEL={用户选择的模型} \
SIZE={比例，可选} \
OUTPUT_NAME={输出名} \
PROMPT="{最终提示词}" \
IMAGE_PATHS="ref_cat.jpg|ref_dog.jpg" \
python3 generate_media.py
```

**视频额外参数：**
- `FIRST_FRAME="ref_cat.jpg"` — 首帧图（图生视频）
- `LAST_FRAME="ref_dog.jpg"` — 尾帧图（首尾帧生视频）

**同步模式：**
```bash
MODE=sync MODEL=nano-banana ... python3 generate_media.py
```

**IMAGE_PATHS 传参规则（通用）：**
- 只传文件名，多张用 `|` 分隔
- ❌ 不要传完整路径，不要带 `reference_images/` 前缀

**异步流程：**
1. 输出 `TASK_ID` + `TASK_STATUS: queued`
2. 每 5 秒轮询，最长 10 分钟
3. 完成后自动下载，输出 `OUTPUT_FILENAME: {文件名}`
4. 自动记录到 `gen/history.json`

> ⚠️ 视频 `seconds` 参数有 bug，暂不传。1.5 Pro 不支持 input_reference。

---

## 五、发送结果到飞书

根据文件类型选择不同 API。

### 判断文件类型

- `.png` `.jpg` `.jpeg` `.gif` `.webp` → 图片
- `.mp4` `.mov` `.avi` → 视频

### 发送图片

```bash
# 1. 获取 token（同凭据管理）
TOKEN=...

# 2. 上传图片
IMAGE_KEY=$(curl -s -X POST "https://open.feishu.cn/open-apis/im/v1/images" \
  -H "Authorization: Bearer $TOKEN" \
  -F "image_type=message" \
  -F "image=@{baseDir}/gen/{文件名}" | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['image_key'])")

# 3. 发送
# 群聊: receive_id_type=chat_id，receive_id 从 chat_id 获取
# 私聊: receive_id_type=open_id，receive_id 从 sender_id 获取
curl -s -X POST "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"receive_id":"{目标ID}","msg_type":"image","content":"{\"image_key\":\"'"$IMAGE_KEY"'\"}"}'
```

### 发送视频

```bash
# 2. 上传视频文件（注意用 /im/v1/files）
FILE_KEY=$(curl -s --max-time 120 -X POST "https://open.feishu.cn/open-apis/im/v1/files" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file_type=stream" \
  -F "file_name={文件名}" \
  -F "file=@{baseDir}/gen/{文件名}" | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['file_key'])")

# 3. 发送文件消息
curl -s -X POST "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"receive_id":"{目标ID}","msg_type":"file","content":"{\"file_key\":\"'"$FILE_KEY"'\"}"}'
```

> 视频文件较大，上传超时建议 120 秒。

---

## 六、失败处理

生成失败时，**立即告知用户**，不要自动重试。报告：站点、模型、提示词摘要、参考图、报错信息。

```bash
cd {baseDir} && python3 generate_media.py --check
```

将可用站点告知用户，由用户决定是否切换站点重试。

**❌ 禁止自动重试。是否重试、用哪个站点，由用户决定。**

> ⚠️ **禁止私自调用生成接口**：失败后排查只允许用 `--check` 检测端点延迟/连通性，**不允许**调用 `/v1/videos` 或 `/v1/chat/completions` 等生成接口。如需调用生成接口排查问题，必须先告知用户并获得确认。

---

## 七、辅助命令

```bash
# 检查站点可用性和延迟
python3 generate_media.py --check

# 查看所有可用模型
python3 generate_media.py --models

# 查看生成历史
cat {baseDir}/gen/history.json | python3 -m json.tool
```

---

## 八、禁止事项

- ❌ 未确认就生成
- ❌ 不下载图片就直接当参考图使用
- ❌ 把 image_key 当本地路径
- ❌ IMAGE_PATHS 传完整路径或带目录前缀
- ❌ 下载后不验证文件有效性
- ❌ 让脚本负责发飞书消息（发送由 agent 负责）
- ❌ 自动重试 API 失败
- ❌ 失败后私自调用生成接口排查（只能用 --check 检测连通性，调用生成接口必须先获得用户确认）
- ❌ 切换模型后不重新确认就执行
