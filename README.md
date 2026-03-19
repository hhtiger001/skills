# Skills

OpenClaw Skills 集合

## 目录

| Skill | 说明 |
|-------|------|
| media-generator | 使用 tu-zi.com API 生成图片和视频，支持参考图、自动选站、异步/同步模式 |
| minimax-understand-image | MiniMax 图像理解 |
| minimax-web-search | MiniMax 网络搜索 |

## 安装

```bash
git clone https://github.com/hhtiger001/skills.git
cp -r skills/* ~/.openclaw/skills/
```

## media-generator

统一的图片和视频生成技能，基于 tu-zi.com API。

### 功能特性

- 🖼️ **图片生成**：支持 Gemini、NanoBanana 等多种模型
- 🎬 **视频生成**：支持豆包 Seedance 系列模型
- 🖼️ **参考图**：支持上传参考图辅助生成
- 🔄 **异步模式**：默认异步，任务队列机制
- ⚡ **自动选站**：并行检测 5 个站点，自动选最快
- 📝 **历史记录**：自动记录每次生成
- 📊 **模型外置**：models.json 统一管理模型/站点/尺寸

### 模型价格

| 分类 | 最便宜 | 性价比 |
|------|--------|--------|
| 图片（异步） | gemini-3-pro-image-preview-async $0.06/次 | gemini-3-pro-image-preview-4k-async $0.20/次 |
| 视频 | doubao-seedance-1-0-lite_480p $0.07/秒 | doubao-seedance-1-0-lite_720p $0.17/秒 |

### 站点

- api.ourzhishi.top（广州）
- apius.tu-zi.com（CDN备用）
- apicdn.tu-zi.com（CDN备用）
- api.tu-zi.com（主站点）
- api.sydney-ai.com（美国）
