"""
Ollama Proxy - 伪装成 Ollama 服务，把请求转发到任意 OpenAI 兼容提供商
用法: python OllamaProxy.py
依赖: pip install flask requests
"""

import json
import time
import requests
from flask import Flask, request, Response, stream_with_context

# ============================================================
#  在这里填写你的配置
# ============================================================
BASE_URL = "https://api.sample.com/v1"   # 你的提供商地址
API_KEY  = "sk-xxxxxxxxxxxxxxxxxxxxxxxx"    # 你的 API Key
# 默认模型（VS 选 Ollama 时如果没指定具体模型则用这个）
DEFAULT_MODEL = "deepseek-chat"
# 对外暴露的"假模型列表"（VS 会从这里选择）
EXPOSED_MODELS = [
    "deepseek-chat",
    "deepseek-reasoner",
]
# ============================================================

app = Flask(__name__)
UPSTREAM = BASE_URL.rstrip("/")
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}


# ══════════════════════════════════════════════════════════════
#  /v1/ 路由 —— VS 实际走这里（OpenAI SDK 格式）
# ══════════════════════════════════════════════════════════════

@app.route("/v1/models", methods=["GET"])
def v1_models():
    """VS 用 OpenAI SDK 拉模型列表"""
    models = [
        {
            "id": m,
            "object": "model",
            "created": 1700000000,
            "owned_by": "proxy",
        }
        for m in EXPOSED_MODELS
    ]
    return {"object": "list", "data": models}


@app.route("/v1/chat/completions", methods=["POST"])
def v1_chat_completions():
    """核心：直接透传到上游，几乎零转换"""
    body = request.get_json(silent=True) or {}
    stream = body.get("stream", False)

    try:
        resp = requests.post(
            f"{UPSTREAM}/chat/completions",
            headers=HEADERS,
            json=body,
            stream=stream,
            timeout=120,
        )
        resp.raise_for_status()
    except requests.HTTPError as e:
        return {"error": str(e)}, resp.status_code
    except Exception as e:
        return {"error": str(e)}, 500

    if not stream:
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {"error": "Upstream returned non-JSON response", "detail": resp.text[:200]}, resp.status_code

    # 流式：原样透传 SSE
    def generate():
        for chunk in resp.iter_content(chunk_size=None):
            yield chunk

    return Response(
        stream_with_context(generate()),
        content_type=resp.headers.get("Content-Type", "text/event-stream"),
    )


# ══════════════════════════════════════════════════════════════
#  /api/ 路由 —— Ollama 原生格式（备用）
# ══════════════════════════════════════════════════════════════

@app.route("/api/tags", methods=["GET"])
def api_tags():
    models = [
        {
            "name": m,
            "model": m,
            "modified_at": "2025-01-01T00:00:00Z",
            "size": 0,
            "digest": "proxy",
            "details": {
                "format": "",
                "family": "custom",
                "parameter_size": "",
                "quantization_level": "",
            },
        }
        for m in EXPOSED_MODELS
    ]
    return {"models": models}


@app.route("/api/show", methods=["POST"])
def api_show():
    body = request.get_json(silent=True) or {}
    model = body.get("model", DEFAULT_MODEL)
    return {
        "modelfile": f"# proxy -> {BASE_URL}",
        "parameters": "",
        "template": "",
        "details": {"format": "", "family": "custom", "parameter_size": "", "quantization_level": ""},
        "model_info": {"general.name": model},
    }


@app.route("/api/chat", methods=["POST"])
def api_chat():
    body = request.get_json(silent=True) or {}
    model = body.get("model", DEFAULT_MODEL)
    stream = body.get("stream", True)

    messages = []
    for msg in body.get("messages", []):
        messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

    payload = {"model": model, "messages": messages, "stream": stream}
    url = f"{UPSTREAM}/chat/completions"

    if not stream:
        try:
            resp = requests.post(url, headers=HEADERS, json=payload, timeout=120)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return {
                "model": model,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "message": {"role": "assistant", "content": content},
                "done": True,
                "done_reason": "stop",
            }
        except Exception as e:
            return {"error": str(e)}, 500

    def generate():
        with requests.post(url, headers=HEADERS, json=payload, stream=True, timeout=120) as resp:
            for line in resp.iter_lines():
                if not line:
                    continue
                line = line.decode("utf-8")
                if line.startswith("data: "):
                    line = line[6:]
                if line == "[DONE]":
                    break
                try:
                    chunk = json.loads(line)
                    choices = chunk.get("choices", [])
                    if choices:
                        content = choices[0].get("delta", {}).get("content", "")
                        done = choices[0].get("finish_reason") is not None
                        yield json.dumps({
                            "model": model,
                            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "message": {"role": "assistant", "content": content},
                            "done": done,
                        }, ensure_ascii=False) + "\n"
                except json.JSONDecodeError:
                    continue

    return Response(stream_with_context(generate()), mimetype="application/x-ndjson")


# ══════════════════════════════════════════════════════════════
#  健康检查
# ══════════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
@app.route("/api/version", methods=["GET"])
def version():
    return {"version": "0.1.0-proxy"}


if __name__ == "__main__":
    print("=" * 50)
    print(f"  Ollama Proxy 启动")
    print(f"  转发目标 : {BASE_URL}")
    print(f"  监听地址 : http://localhost:11434")
    print(f"  暴露模型 : {', '.join(EXPOSED_MODELS)}")
    print("=" * 50)
    app.run(host="0.0.0.0", port=11434, debug=False)