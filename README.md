# OpenClaw Skills

一组用于 [OpenClaw](https://github.com/openclaw/openclaw) 的 Agent Skills，提供图片/视频生成、图像理解、网络搜索等能力。

## Skills 列表

| Skill | 说明 | 依赖 |
|-------|------|------|
| [media-generator](media-generator/) | 使用 tu-zi.com API 生成图片和视频，支持参考图、自动选站、异步/同步模式 | tu-zi API Key |
| [minimax-understand-image](minimax-understand-image/) | 使用 MiniMax MCP 进行图像理解和分析 | MiniMax API Key、uvx |
| [minimax-web-search](minimax-web-search/) | 使用 MiniMax MCP 进行网络搜索 | MiniMax API Key、uvx |

## 安装

### 前置要求

- [OpenClaw](https://github.com/openclaw/openclaw) 已安装并运行
- Python 3.9+
- Node.js 18+（用于 MCP 服务）

### 安装步骤

```bash
# 1. 克隆仓库
git clone https://github.com/hhtiger001/skills.git
cd skills

# 2. 复制到 OpenClaw skills 目录
cp -r media-generator ~/.openclaw/skills/
cp -r minimax-understand-image ~/.openclaw/skills/
cp -r minimax-web-search ~/.openclaw/skills/

# 3. 配置 API Key
# media-generator: 编辑 ~/.openclaw/skills/media-generator/scripts/.tuzi_api_key
# minimax 系列: 编辑 ~/.mcporter/mcporter.json，填入 MiniMax API Key
```

### 配置说明

#### media-generator

需要 tu-zi.com API Key：

```bash
# 获取 API Key: https://api.tu-zi.com/token
echo "sk-your-api-key" > ~/.openclaw/skills/media-generator/scripts/.tuzi_api_key
chmod 600 ~/.openclaw/skills/media-generator/scripts/.tuzi_api_key
```

#### minimax-understand-image / minimax-web-search

需要配置 [mcporter](https://github.com/isaacwaker/mcporter)：

```bash
# 安装 mcporter
npm install -g mcporter

# 创建配置文件
mkdir -p ~/.mcporter
cat > ~/.mcporter/mcporter.json << 'EOF'
{
  "mcpServers": {
    "minimax": {
      "command": "uvx",
      "args": ["minimax-mcp"],
      "env": {
        "MINIMAX_API_KEY": "your-api-key"
      }
    }
  }
}
EOF

# 验证
mcporter list
```

## 使用

Skills 安装完成后，OpenClaw 会自动识别并根据用户消息触发对应 Skill：

- **生成图片/视频**：发送"生成图片"或"生成视频"，可附带参考图
- **图像理解**：发送图片并要求分析/描述
- **网络搜索**：要求搜索在线信息

## 项目结构

```
skills/
├── media-generator/           # 图片/视频生成
│   ├── SKILL.md               # Skill 定义
│   └── scripts/
│       ├── generate_media.py  # 生成脚本
│       ├── models.json        # 模型/站点配置
│       └── gen/               # 生成产物（自动创建）
├── minimax-understand-image/  # 图像理解
│   ├── SKILL.md
│   └── scripts/
├── minimax-web-search/        # 网络搜索
│   ├── SKILL.md
│   └── scripts/
└── README.md
```

## License

MIT
