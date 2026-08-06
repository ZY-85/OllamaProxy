# OllamaProxy

一个轻量级代理，把自己伪装成 Ollama 服务，让 VS2026 / SSMS22 的 Copilot 可以使用任意云端大模型。

```
VS2026 / SSMS22 Copilot ── Ollama 协议 ──→ OllamaProxy ── OpenAI 协议 ──→ 云端 API
                        localhost:11434                   (DeepSeek / OpenAI / Claude / ...)
```

## 设计思路

**代理做两件事：路由和改名。**

VS2026 Copilot 本身就是 OpenAI 协议栈——它不需要格式转换，只需要代理帮它找到正确的上游。代理的核心逻辑非常简单：

1. **模型名映射**：`ollama_name`（对 Copilot 暴露）↔ `upstream_name`（真实 API 模型名）
2. **请求路由**：根据模型名查找对应的 `base_url` + `api_key`，转发到正确上游
3. **响应改名**：把上游响应里的模型名换回 `ollama_name`，Copilot 全程看到一致的名字

```
请求方向:  ollama_name → upstream_name  （v4flash → deepseek-chat）
响应方向:  upstream_name → ollama_name  （deepseek-chat → v4flash）
```

Copilot 始终看到 `v4flash`，代理内部处理名字映射，上游只认识 `deepseek-chat`。

代理不是能力网关——每个模型的能力写在配置文件里，`/api/show` 如实报告，上游 API 能不能处理 tools、images 是上游的事。

## 配置

配置文件放在 `%USERPROFILE%\.ollama-proxy\config.yaml`，也可以通过命令行指定其他位置。

```yaml
# ~/.ollama-proxy/config.yaml

server:
  host: "127.0.0.1"
  port: 11434
  timeout: 120

models:
  - ollama_name: "deepseek-chat"
    upstream_name: "deepseek-chat"
    base_url: "https://api.deepseek.com/v1"
    api_key: "sk-xxxxxxxx"
    context_length: 128000
    capabilities:
      - completion
      - tools

  - ollama_name: "deepseek-v3"
    upstream_name: "deepseek-chat"
    base_url: "https://api.deepseek.com/v1"
    api_key: "sk-xxxxxxxx"
    context_length: 128000
    capabilities:
      - completion
      - tools

  - ollama_name: "claude-sonnet"
    upstream_name: "claude-sonnet-5-20251001"
    base_url: "https://api.anthropic.com/v1"
    api_key: "sk-ant-xxxxxxxx"
    context_length: 200000
    capabilities:
      - completion
      - tools
      - vision
```

### 配置项说明

| 字段 | 必填 | 说明 |
|---|---|---|
| `ollama_name` | ✅ | 对 Copilot 暴露的模型名 |
| `upstream_name` | ✅ | 发给上游 API 的 model 参数 |
| `base_url` | ✅ | 上游 API 地址（OpenAI 兼容格式） |
| `api_key` | ✅ | 上游 API Key |
| `context_length` | 否 (默认 128000) | 上下文窗口大小，报告给 Copilot |
| `capabilities` | 否 (默认 `[completion]`) | 模型能力：`completion`、`tools`、`vision` |

### 配置加载顺序

1. 命令行 `-c <path>` 指定的路径
2. 环境变量 `OLLAMA_PROXY_CONFIG`
3. 默认路径 `%USERPROFILE%\.ollama-proxy\config.yaml`

## Copilot 交互流程

VS2026 Copilot 的交互模式是**混合协议**：

- **模型发现** → Ollama 端点 (`/api/tags`, `/api/show`)
- **对话通信** → OpenAI 端点 (`/v1/chat/completions`)

代理不需要做 OpenAI ↔ Ollama 格式转换，因为 Copilot 内部本身就讲 OpenAI 协议。代理的核心逻辑是**模型名映射**：

```
Copilot                        代理                          上游 API
───────────                    ──────────                    ────────
GET /api/tags  ──────────→  返回 ollama_name 列表
POST /api/show ──────────→  返回 capabilities + ctx_len    （不转发）

POST /v1/chat/completions
  model: "v4flash"    ──→  改名 upstream_name ──→  POST /chat/completions
  messages: [...]           转发到 base_url         model: "deepseek-chat"
  stream: true                                      messages: [...]
  tools: [...]                                      stream: true
                                                    tools: [...]

                          ←  响应中 model 名换回   ←  SSE / JSON
                              upstream_name → ollama_name
                              Copilot 始终看到 "v4flash"
```

## 端点

| 端点 | 方法 | Copilot 用途 | 说明 |
|---|---|---|---|
| `/` | GET | 探测服务存活 | 返回版本号 |
| `/api/tags` | GET | 拉取可用模型列表 | Ollama 格式 |
| `/api/show` | POST | 查询模型能力和上下文 | 按配置文件回答 |
| `/v1/models` | GET | 模型列表（OpenAI 格式） | 兼容部分客户端 |
| `/v1/chat/completions` | POST | **Copilot 实际对话端点** | 替换 model 名后透传，响应中换回 ollama_name |
| `/api/chat` | POST | Ollama 原生对话 | Ollama ↔ OpenAI 格式转换，给原生 Ollama 客户端用 |

## 快速开始

### 1. 安装依赖

```bash
pip install flask requests pyyaml
```

### 2. 创建配置文件

在 `%USERPROFILE%\.ollama-proxy\` 下创建 `config.yaml`，填入你的 API 信息和模型列表。

### 3. 启动

```bash
python proxy.py
```

### 4. 在 VS2026 / SSMS22 中使用

1. 打开 Copilot Chat → 模型下拉 → Manage Models
2. 选择 Ollama 作为 Provider
3. 将 Endpoint 指向 `http://localhost:11434`
4. 在模型列表中选择你配置的模型

## 验证

```bash
# 健康检查
curl http://localhost:11434/

# 模型列表
curl http://localhost:11434/api/tags

# 模型详情
curl -X POST http://localhost:11434/api/show -H "Content-Type: application/json" -d "{\"model\":\"deepseek-chat\"}"

# 对话测试（Ollama 原生格式）
curl http://localhost:11434/api/chat -H "Content-Type: application/json" -d "{\"model\":\"deepseek-chat\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}],\"stream\":false}"

# 对话测试（OpenAI 格式 — Copilot 实际使用的端点）
curl http://localhost:11434/v1/chat/completions -H "Content-Type: application/json" -d "{\"model\":\"deepseek-chat\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}],\"stream\":false}"
```

## 协议与作者

- **作者**：ZY
- **许可证**：[MIT License](https://opensource.org/licenses/MIT)
