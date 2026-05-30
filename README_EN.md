# OllamaProxy

A lightweight local proxy service that masquerades as an Ollama service and forwards incoming requests to any OpenAI-compatible API provider (e.g., DeepSeek, Zhipu GLM, SiliconFlow, etc.).

With this proxy, you can let Visual Studio Copilot or other tools that only support Ollama local models seamlessly connect to cloud LLM services without modifying any client code.

> [中文文档](README.md)

## Table of Contents

- [Core Features](#-core-features)
- [Architecture](#-architecture)
- [Quick Start](#-quick-start)
- [Route Reference](#-route-reference)
- [Verification](#-verification)
- [Limitations](#-limitations)
- [License & Author](#-license--author)

## ✨ Core Features

- **Dual-Protocol Support**: Handles both `/v1/` (OpenAI SDK format) and `/api/` (Ollama native format) routes. Most modern tools like VS Copilot use `/v1/`, while legacy tools that only speak Ollama's native protocol fall back to `/api/`.
- **Streaming Support**: Fully handles non-streaming and streaming (SSE / NDJSON) responses for token-by-token output.
- **Smart Routing**: `/v1/chat/completions` is a zero-conversion passthrough with virtually no overhead; `/api/chat` automatically handles bidirectional Ollama ↔ OpenAI format conversion.
- **Seamless Integration**: Listens on Ollama's default port `11434` — VS / Copilot and similar tools recognize it with zero extra configuration.

## 🏗 Architecture

```
┌──────────────────┐     HTTP      ┌──────────────────┐     HTTPS     ┌──────────────────┐
│  VS Copilot /    │ ───────────▶  │   OllamaProxy    │ ───────────▶  │  Upstream API    │
│  Ollama Clients   │  localhost    │   :11434         │               │  (OpenAI-compat) │
│                  │ ◀───────────  │                  │ ◀───────────  │                  │
└──────────────────┘               └──────────────────┘               └──────────────────┘
                                         │
                        ┌────────────────┼────────────────┐
                        ▼                                 ▼
                   /v1/* Routes                       /api/* Routes
                (OpenAI SDK Format)             (Ollama Native Format)
                Zero-conversion passthru     Bidirectional format conversion
```

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install flask requests
```

### 2. Configure

Open `OllamaProxy.py` and fill in your upstream provider details in the configuration section at the top:

```python
BASE_URL = "https://api.deepseek.com/v1"        # Upstream OpenAI-compatible endpoint
API_KEY  = "***"                # Your API key
DEFAULT_MODEL = "deepseek-v4-flash"              # Default model
EXPOSED_MODELS = [                               # Models exposed to clients
    "deepseek-v4-flash",
    "deepseek-v4-pro"
]
```

> ⚠️ **Security Reminder**: API keys are sensitive credentials. Do not commit configuration files containing real keys to Git repositories.

### 3. Start the Proxy

```bash
python OllamaProxy.py
```

Once started, the proxy listens on `http://localhost:11434` and prints a banner:

```
==================================================
  Ollama Proxy Started
  Upstream  : https://api.deepseek.com/v1
  Listening : http://localhost:11434
  Models    : deepseek-v4-flash, deepseek-v4-pro
==================================================
```

### 4. Use in Visual Studio

1. Install and enable Copilot or any Ollama-compatible extension in VS.
2. Point the Ollama service URL to `http://localhost:11434` (no changes needed if the extension defaults to local Ollama).
3. Select one of the models you configured in `EXPOSED_MODELS` and start chatting.

## 📖 Route Reference

| Path | Method | Description | Use Case |
| :--- | :--- | :--- | :--- |
| `/v1/models` | GET | Returns model list in OpenAI format | VS fetches models via OpenAI SDK |
| `/v1/chat/completions` | POST | Passthrough to upstream `/chat/completions` | Core chat route for VS (zero conversion) |
| `/api/tags` | GET | Returns model list in Ollama format | Ollama native clients fetching models |
| `/api/show` | POST | Returns model details in Ollama format | Ollama native clients inspecting models |
| `/api/chat` | POST | Bidirectional Ollama ↔ OpenAI conversion | Ollama native client chat (fallback) |
| `/` or `/api/version` | GET | Health check / version info | Verify the proxy is alive |

## 🧪 Verification

Use `curl` to quickly verify the proxy is working after startup:

```bash
# Health check
curl http://localhost:11434/api/version

# List models (OpenAI format)
curl http://localhost:11434/v1/models

# List models (Ollama format)
curl http://localhost:11434/api/tags

# Test chat (non-streaming)
curl http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"Hello"}]}'
```

## ⚠️ Limitations

- **Chat Completions Only**: Does not support `/api/generate` (Ollama generate endpoint), `/api/embeddings`, or other non-chat endpoints.
- **No Model Management**: Does not support `/api/pull` (download models), `/api/push` (upload models), etc.
- **Single-Process**: Built on Flask's development server; not suitable for high-concurrency production workloads.
- **Upstream Format Dependency**: The upstream API must be fully compatible with OpenAI's Chat Completions format.

## 📄 License & Author

- **Author**: ZY
- **License**: [MIT License](https://opensource.org/licenses/MIT)
