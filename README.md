# OllamaProxy

一个轻量级的本地代理服务，旨在将自身伪装为 Ollama 服务，并将接收到的请求转发至任意 OpenAI 兼容的 API 提供商（如 DeepSeek、智谱 GLM、硅基流动等）。

通过此代理，你可以让 Visual Studio 的 Copilot 或其他默认仅支持 Ollama 本地模型的工具，直接无缝接入云端大模型服务，无需修改客户端代码。

> [English](README_EN.md)

## 目录

- [核心特性](#-核心特性)
- [架构](#-架构)
- [快速开始](#-快速开始)
- [路由说明](#-路由说明)
- [验证代理](#-验证代理)
- [局限性](#-局限性)
- [协议与作者](#-协议与作者)

## ✨ 核心特性

- **双协议兼容**：同时支持 `/v1/`（OpenAI SDK 格式）和 `/api/`（Ollama 原生格式）路由。VS Copilot 等多数现代工具走 `/v1/`，而某些仅支持 Ollama 原生协议的老工具走 `/api/` 备用。
- **流式支持**：完美处理非流式与流式（SSE / NDJSON）响应，支持逐字输出体验。
- **智能路由**：`/v1/chat/completions` 零转换透传，几乎无开销；`/api/chat` 自动完成 Ollama ↔ OpenAI 格式双向转换。
- **无缝集成**：监听 Ollama 默认端口 `11434`，VS / Copilot 等工具无需额外配置即可识别。

## 🏗 架构

```
┌──────────────────┐     HTTP      ┌──────────────────┐     HTTPS     ┌──────────────────┐
│  VS Copilot /    │ ───────────▶  │   OllamaProxy    │ ───────────▶  │  上游 API 提供商   │
│  其他 Ollama 工具  │  localhost    │   :11434         │               │  (OpenAI 兼容)    │
│                  │ ◀───────────  │                  │ ◀───────────  │                  │
└──────────────────┘               └──────────────────┘               └──────────────────┘
                                         │
                        ┌────────────────┼────────────────┐
                        ▼                                 ▼
                   /v1/* 路由                         /api/* 路由
                (OpenAI SDK 格式)                (Ollama 原生格式)
                   零转换透传                    请求/响应格式双向转换
```

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install flask requests
```

### 2. 修改配置

打开 `OllamaProxy.py`，在顶部的配置区域填写你的上游提供商信息：

```python
BASE_URL = "https://api.deepseek.com/v1"        # 上游 OpenAI 兼容地址
API_KEY  = "sk-your-api-key-here"                # 你的 API Key
DEFAULT_MODEL = "deepseek-v4-flash"              # 默认模型
EXPOSED_MODELS = [                               # 对外暴露的模型列表
    "deepseek-v4-flash",
    "deepseek-v4-pro"
]
```

> ⚠️ **安全提醒**：API Key 是敏感信息，请勿将包含真实 Key 的配置文件提交到 Git 仓库。

### 3. 启动代理

```bash
python OllamaProxy.py
```

启动成功后，代理将监听在 `http://localhost:11434`，终端会打印如下信息：

```
==================================================
  Ollama Proxy 启动
  转发目标 : https://api.deepseek.com/v1
  监听地址 : http://localhost:11434
  暴露模型 : deepseek-v4-flash, deepseek-v4-pro
==================================================
```

### 4. 在 Visual Studio 中使用

1. 在 VS 中安装并启用 Copilot 或其他支持 Ollama 的扩展。
2. 将 Ollama 的服务地址指向 `http://localhost:11434`（如果扩展默认寻找本地 Ollama，则无需修改）。
3. 在模型列表中选择你在 `EXPOSED_MODELS` 中配置的模型名称即可开始使用。

## 📖 路由说明

| 路径 | 方法 | 说明 | 适用场景 |
| :--- | :--- | :--- | :--- |
| `/v1/models` | GET | 返回 OpenAI 格式的模型列表 | VS 使用 OpenAI SDK 拉取模型 |
| `/v1/chat/completions` | POST | 原样透传至上游 `/chat/completions` | VS 实际对话核心路由（零转换） |
| `/api/tags` | GET | 返回 Ollama 格式的模型列表 | Ollama 原生客户端拉取模型 |
| `/api/show` | POST | 返回 Ollama 格式的模型详情 | Ollama 原生客户端查看模型信息 |
| `/api/chat` | POST | Ollama → OpenAI 格式双向转换 | Ollama 原生客户端对话（备用） |
| `/` 或 `/api/version` | GET | 健康检查/版本信息 | 确认代理是否存活 |

## 🧪 验证代理

启动代理后，可以用 `curl` 快速验证是否正常工作：

```bash
# 健康检查
curl http://localhost:11434/api/version

# 查看模型列表（OpenAI 格式）
curl http://localhost:11434/v1/models

# 查看模型列表（Ollama 格式）
curl http://localhost:11434/api/tags

# 测试对话（非流式）
curl http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"你好"}]}'
```

## ⚠️ 局限性

- **仅支持 Chat Completions**：不支持 `/api/generate`（Ollama generate 端点）、`/api/embeddings`（向量嵌入）等端点。
- **不支持模型管理**：不支持 `/api/pull`（下载模型）、`/api/push`（上传模型）等操作。
- **单进程运行**：基于 Flask 开发服务器，不适合高并发生产环境。
- **依赖上游格式兼容**：要求上游 API 必须完全兼容 OpenAI 的 Chat Completions 格式。

## 📄 协议与作者

- **作者**：ZY
- **许可证**：[MIT License](https://opensource.org/licenses/MIT)
