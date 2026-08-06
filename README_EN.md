# OllamaProxy

A lightweight proxy that masquerades as an Ollama service, enabling VS2026 / SSMS22 Copilot to use any cloud LLM.

```
VS2026 / SSMS22 Copilot ── Ollama Protocol ──→ OllamaProxy ── OpenAI Protocol ──→ Cloud API
                        localhost:11434                   (DeepSeek / OpenAI / Claude / ...)
```

## Design

**The proxy does two things: route and rename.**

VS2026 Copilot natively speaks OpenAI protocol — it doesn't need format translation, just a proxy to route requests to the correct upstream. The core logic:

1. **Model name mapping**: `ollama_name` (exposed to Copilot) ↔ `upstream_name` (real API model name)
2. **Request routing**: look up `base_url` + `api_key` by model name, forward to the right upstream
3. **Response renaming**: replace the upstream model name in responses back to `ollama_name`, so Copilot sees a consistent name

```
Request:   ollama_name → upstream_name  (v4flash → deepseek-chat)
Response:  upstream_name → ollama_name  (deepseek-chat → v4flash)
```

Copilot always sees `v4flash`. The proxy handles name mapping internally. The upstream only knows `deepseek-chat`.

The proxy is not a capability gate — model capabilities are declared in config, `/api/show` reports them honestly, and the upstream API decides whether it can actually handle tools or images.

## Configuration

Config file at `%USERPROFILE%\.ollama-proxy\config.yaml`. A custom path can be passed via command line.

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

### Fields

| Field | Required | Description |
|---|---|---|
| `ollama_name` | ✅ | Model name exposed to Copilot |
| `upstream_name` | ✅ | Model name sent to upstream API |
| `base_url` | ✅ | Upstream API endpoint (OpenAI-compatible) |
| `api_key` | ✅ | Upstream API key |
| `context_length` | No (default 128000) | Context window size reported to Copilot |
| `capabilities` | No (default `[completion]`) | Model capabilities: `completion`, `tools`, `vision` |

### Config Loading Order

1. Command line `-c <path>`
2. Environment variable `OLLAMA_PROXY_CONFIG`
3. Default path `%USERPROFILE%\.ollama-proxy\config.yaml`

## Copilot Interaction Flow

VS2026 Copilot uses a **hybrid protocol**:

- **Model discovery** → Ollama endpoints (`/api/tags`, `/api/show`)
- **Chat** → OpenAI endpoint (`/v1/chat/completions`)

No Ollama ↔ OpenAI format translation is needed — Copilot speaks OpenAI natively. The proxy only handles model name mapping:

```
Copilot                        Proxy                         Upstream
───────────                    ──────────                    ────────
GET /api/tags  ──────────→  Return ollama_name list
POST /api/show ──────────→  Return capabilities + ctx_len   (not forwarded)

POST /v1/chat/completions
  model: "v4flash"    ──→  Rename to upstream_name ──→  POST /chat/completions
  messages: [...]           Forward to base_url          model: "deepseek-chat"
  stream: true                                          messages: [...]
  tools: [...]                                          stream: true
                                                        tools: [...]

                          ←  Rename model in response  ←  SSE / JSON
                              upstream_name → ollama_name
                              Copilot always sees "v4flash"
```

## Endpoints

| Endpoint | Method | Purpose | Notes |
|---|---|---|---|
| `/` | GET | Health check | Returns version |
| `/api/tags` | GET | Model discovery | Ollama format |
| `/api/show` | POST | Model capabilities & context | Answered from config |
| `/v1/models` | GET | Model list (OpenAI format) | For clients that prefer it |
| `/v1/chat/completions` | POST | **Copilot's actual chat endpoint** | Rename model → forward → rename back |
| `/api/chat` | POST | Ollama native chat | Ollama ↔ OpenAI format conversion, for native Ollama clients |

## Quick Start

### 1. Install Dependencies

```bash
pip install flask requests pyyaml
```

### 2. Create Config

Create `config.yaml` under `%USERPROFILE%\.ollama-proxy\` with your API credentials and model list.

### 3. Start

```bash
python proxy.py
```

### 4. Use in VS2026 / SSMS22

1. Open Copilot Chat → model dropdown → Manage Models
2. Select Ollama as the Provider
3. Point the Endpoint to `http://localhost:11434`
4. Pick a model from the list

## Verification

```bash
# Health check
curl http://localhost:11434/

# Model list
curl http://localhost:11434/api/tags

# Model details
curl -X POST http://localhost:11434/api/show -H "Content-Type: application/json" -d "{\"model\":\"deepseek-chat\"}"

# Chat test (Ollama native format)
curl http://localhost:11434/api/chat -H "Content-Type: application/json" -d "{\"model\":\"deepseek-chat\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}],\"stream\":false}"

# Chat test (OpenAI format — the endpoint Copilot actually uses)
curl http://localhost:11434/v1/chat/completions -H "Content-Type: application/json" -d "{\"model\":\"deepseek-chat\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}],\"stream\":false}"
```

## License & Author

- **Author**: ZY
- **License**: [MIT License](https://opensource.org/licenses/MIT)
