---
name: image-generator
description: 使用 tu-zi.com API 生成图片。当用户要求生成图片时触发。工作流程：(1) 优化提示词并给用户选择 (2) 等待用户同意 (3) 执行脚本生成 (4) 将生成的图片通过飞书 API 上传后发送到当前会话。
---

# Image Generator

使用 tu-zi.com 的 gpt-4o-image 模型生成图片。

## 目录结构

```
~/.openclaw/skills/image-generator/scripts/
├── gen/              # 生成的图片
├── logs/             # 请求日志（按月：YYYYMM.log）
├── reference_images/ # 用户上传的参考图
└── generate_image.py # 主脚本
```

## 环境变量

运行脚本前需要设置以下环境变量：

| 变量 | 说明 | 示例 |
|------|------|------|
| `TUZI_API_KEY` | tu-zi.com API Key | `sk-xxx` |
| `FEISHU_APP_ID` | 飞书应用 ID | `cli_xxx` |
| `FEISHU_APP_SECRET` | 飞书应用密钥 | `xxx` |
| `FEISHU_TARGET` | 目标会话 ID | `oc_xxx`(群聊) 或 `ou_xxx`(个人) |

## 工作流程

**严格遵循以下流程：**

### 收到参考图时
1. 用户发送图片到飞书群
2. 保存图片到 `reference_images/`
3. 告知用户保存的文件名

### 生成图片时

1. **第一步：优化提示词** - 用户要求生成图片后：
   - 提供原版/优化版提示词选项
   - 等待用户选择

2. **第二步：确认** - 用户选择后告知使用的提示词和参考图，等待同意

3. **第三步：执行脚本** - 用户同意后运行脚本

   **设置环境变量后执行：**
   ```bash
   cd ~/.openclaw/skills/image-generator/scripts
   TUZI_API_KEY=sk-xxx \
   FEISHU_APP_ID=cli_xxx \
   FEISHU_APP_SECRET=xxx \
   FEISHU_TARGET=当前会话ID \
   python3 generate_image.py
   ```

   - 群聊：FEISHU_TARGET = `chat:oc_xxx` 中的 `oc_xxx`
   - 个人：FEISHU_TARGET = `user:ou_xxx` 中的 `ou_xxx`

4. **第四步：发送图片** - 脚本自动通过飞书 API 上传并发送到当前会话

## 脚本配置

脚本位置：`~/.openclaw/skills/image-generator/scripts/generate_image.py`

**可配置项（顶部）：**
- `IMAGE_PATHS`: 参考图片列表（填文件名）
- `PROMPT`: 生成描述
- `OUTPUT_NAME`: 输出文件名（留空则自动编号）

## 日志

请求日志保存在 `logs/YYYYMM.log`，记录完整请求和响应。

## 飞书图片发送（已集成到脚本）

脚本已自动处理：
1. 生成图片后，自动上传到飞书（获取 image_key）
2. 自动发送到 FEISHU_TARGET 指定的目标会话

## 注意事项

- 每次生成前都必须先告知用户并等待同意
- 生成的图片自动发送到当前会话
- 所有图片统一存放在 `gen/`
