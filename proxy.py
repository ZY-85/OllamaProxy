"""OllamaProxy — Ollama API 兼容代理
============================================================
把自己伪装成 Ollama 服务，接收 Ollama 原生协议请求，
翻译为 OpenAI 兼容格式后转发到云端大模型 API。

目标客户端：VS2026 / SSMS22 Copilot BYOM
============================================================
"""

import argparse
import json
import logging
import os
import sys
import time

import requests
import yaml
from flask import Flask, Response, request, stream_with_context
"""
# ── 按需启用--修复 Windows 控制台编码 ──
if sys.platform == "win32":
    # 激活 ANSI 转义序列支持（部分 Windows 版本需要）
    os.system("chcp 65001 >nul 2>&1")
    # 强制设置标准输出的编码
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
    sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', buffering=1)
"""
# ===================================================================
# 日志
# ===================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ollama-proxy")

# ===================================================================
# 默认值
# ===================================================================

DEFAULT_CONFIG_DIR = os.path.join(os.environ["USERPROFILE"], ".ollama-proxy")
DEFAULT_CONFIG_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.yaml")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11434
DEFAULT_TIMEOUT = 120
DEFAULT_CONTEXT_LENGTH = 128000

app = Flask(__name__)
CONFIG = None  # 启动时从 YAML 加载的完整配置

# ===================================================================
# 配置加载
# ===================================================================


def load_config(path: str) -> dict:
    """加载并校验 YAML 配置文件。"""
    if not os.path.exists(path):
        log.error("配置文件不存在: %s", path)
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if not cfg:
        log.error("配置文件为空: %s", path)
        sys.exit(1)
    if "models" not in cfg or not cfg["models"]:
        log.error("配置文件中没有定义 models 列表")
        sys.exit(1)

    # 给每个模型补默认值
    for m in cfg["models"]:
        m.setdefault("context_length", DEFAULT_CONTEXT_LENGTH)
        m.setdefault("capabilities", ["completion"])

    return cfg


def find_model(ollama_name: str) -> dict | None:
    """按 ollama_name 查找模型配置。兼容带 :tag 后缀的模型名。"""
    if not ollama_name:
        return None
    for m in CONFIG["models"]:
        if m["ollama_name"] == ollama_name:
            return m
    # 兼容 "model:latest" 这种带 tag 的写法
    base = ollama_name.split(":")[0]
    for m in CONFIG["models"]:
        if m["ollama_name"] == base:
            return m
    return None


def server_config() -> dict:
    """获取 server 段配置，缺省字段用默认值填充。"""
    srv = CONFIG.get("server", {})
    return {
        "host": srv.get("host", DEFAULT_HOST),
        "port": int(srv.get("port", DEFAULT_PORT)),
        "timeout": int(srv.get("timeout", DEFAULT_TIMEOUT)),
    }

# ===================================================================
# 格式转换：请求
# ===================================================================

# 常见图片格式的 Base64 特征前缀
_IMAGE_SIGNATURES = [
    ("iVBORw0KGgo", "image/png"),
    ("/9j/", "image/jpeg"),
    ("R0lGOD", "image/gif"),
    ("UklGR", "image/webp"),
    ("Qk", "image/bmp"),
]


def guess_image_mime(b64: str) -> str:
    """根据 Base64 头部特征字节猜测 MIME 类型。"""
    for prefix, mime in _IMAGE_SIGNATURES:
        if b64.startswith(prefix):
            return mime
    return "image/png"


def convert_messages_ollama_to_openai(messages: list) -> tuple[list, bool]:
    """Ollama 消息列表 → OpenAI 消息列表。

    Ollama:  {role, content: str, images?: [base64...]}
    OpenAI:  {role, content: str | [{type:"text",...}, {type:"image_url",...}]}

    返回 (openai_messages, has_images)。
    """
    out = []
    has_images = False

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        images = msg.get("images")

        if images:
            has_images = True
            parts = [{"type": "text", "text": content}]
            for img in images:
                mime = guess_image_mime(img)
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{img}"},
                })
            content = parts

        m = {"role": role, "content": content}

        # assistant 消息可能带有 tool_calls（多轮对话回传）
        if msg.get("tool_calls"):
            m["tool_calls"] = msg["tool_calls"]

        # tool 消息携带 tool_call_id
        if msg.get("tool_call_id"):
            m["tool_call_id"] = msg["tool_call_id"]

        out.append(m)

    return out, has_images


def build_openai_payload(model_cfg: dict, body: dict) -> tuple[dict, bool]:
    """从 Ollama /api/chat 请求体构建 OpenAI /chat/completions 请求体。

    返回 (payload, stream)。
    """
    messages, _ = convert_messages_ollama_to_openai(body.get("messages", []))
    stream = body.get("stream", True)

    payload = {
        "model": model_cfg["upstream_name"],
        "messages": messages,
        "stream": stream,
    }

    # tools 原样转发
    if body.get("tools"):
        payload["tools"] = body["tools"]

    # Ollama options → OpenAI 参数
    opts = body.get("options") or {}
    if "temperature" in opts:
        payload["temperature"] = opts["temperature"]
    if "top_p" in opts:
        payload["top_p"] = opts["top_p"]
    if "num_predict" in opts:
        payload["max_tokens"] = opts["num_predict"]
    if "stop" in opts:
        payload["stop"] = opts["stop"]

    return payload, stream

# ===================================================================
# 格式转换：响应
# ===================================================================


def openai_tool_calls_to_ollama(tool_calls: list) -> list:
    """OpenAI tool_calls → Ollama tool_calls。

    OpenAI 的 function.arguments 是 JSON 字符串；
    Ollama 期待已解析的对象。
    """
    out = []
    for tc in tool_calls or []:
        fn = tc.get("function", {})
        args = fn.get("arguments", "{}")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                pass  # 解析失败保留原始字符串
        out.append({"function": {"name": fn.get("name", ""), "arguments": args}})
    return out


def normalize_finish_reason(reason: str | None) -> str:
    """统一 finish_reason。Ollama 协议不使用 "tool_calls" 作为停止原因。"""
    if reason == "tool_calls":
        return "stop"
    return reason or "stop"


def make_ollama_chunk(model_name: str, *, content: str = "", done: bool = False,
                      done_reason: str | None = None,
                      tool_calls: list | None = None,
                      usage: dict | None = None) -> dict:
    """构建一个 Ollama chat 响应块。"""
    chunk: dict = {
        "model": model_name,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "message": {"role": "assistant", "content": content},
        "done": done,
    }
    if done and done_reason:
        chunk["done_reason"] = done_reason
    if tool_calls:
        chunk["message"]["tool_calls"] = tool_calls
    if usage:
        chunk["prompt_eval_count"] = usage.get("prompt_tokens")
        chunk["eval_count"] = usage.get("completion_tokens")
    return chunk


def assembled_tool_calls_from_acc(tc_acc: dict) -> list:
    """将流式累积的 tool_calls 分片组装为 Ollama 最终格式。"""
    out = []
    for slot in sorted(tc_acc.values(), key=lambda s: s.get("index", 0)):
        args = slot.get("arguments", "")
        if isinstance(args, str) and args:
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                pass
        out.append({
            "function": {
                "name": slot.get("name", ""),
                "arguments": args if args else {},
            }
        })
    return out

# ===================================================================
# 端点
# ===================================================================


@app.route("/", methods=["GET"])
def health():
    """健康检查 / 版本探测。"""
    return {"version": "1.0.0"}


@app.route("/api/tags", methods=["GET"])
def api_tags():
    """模型列表。Copilot 用此接口发现可用模型。"""
    models_list = [
        {"name": m["ollama_name"], "model": m["ollama_name"]}
        for m in CONFIG["models"]
    ]
    return {"models": models_list}


@app.route("/api/show", methods=["POST"])
def api_show():
    """模型能力查询。
    Copilot 在用户选定模型后调用，读取 capabilities 和 context_length。
    不需要转发到上游——直接按配置文件回答。
    """
    body = request.get_json(silent=True) or {}
    model_name = body.get("model", "")

    model_cfg = find_model(model_name)
    if not model_cfg:
        return {"error": f"unknown model '{model_name}'"}, 404

    ctx_len = model_cfg["context_length"]
    capabilities = model_cfg["capabilities"]

    return {
        "parameters": f"num_ctx {ctx_len}",
        "model_info": {"general.context_length": ctx_len},
        "capabilities": capabilities,
    }


@app.route("/v1/models", methods=["GET"])
def v1_models():
    """OpenAI 格式模型列表。部分客户端（包括 VS2026 Copilot）
    可能用此端点而非 /api/tags 来发现模型。"""
    data = [
        {"id": m["ollama_name"], "object": "model",
         "created": 1700000000, "owned_by": "proxy"}
        for m in CONFIG["models"]
    ]
    return {"object": "list", "data": data}


@app.route("/v1/chat/completions", methods=["POST"])
def v1_chat_completions():
    """OpenAI 格式透传——VS2026 Copilot 实际使用的对话端点。

    与 /api/chat 的关键区别：
    - /api/chat：Ollama ↔ OpenAI 格式转换（给原生 Ollama 客户端用）
    - /v1/chat/completions：纯透传，请求和响应都不转格式（Copilot 原生讲 OpenAI 协议）

    只做一件事：按模型名查配置，把请求转发到对应的上游。
    """
    body = request.get_json(silent=True) or {}
    model_name = body.get("model", "")

    model_cfg = find_model(model_name)
    if not model_cfg:
        return {"error": f"unknown model '{model_name}'"}, 404

    base_url = model_cfg["base_url"].rstrip("/")
    api_key = model_cfg["api_key"]
    timeout = server_config()["timeout"]

    # 替换为上游模型名，其余字段原样透传
    body["model"] = model_cfg["upstream_name"]
    stream = body.get("stream", True)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = f"{base_url}/chat/completions"

    log.info("/v1/chat/completions model=%s -> upstream=%s stream=%s",
             model_name, model_cfg["upstream_name"], stream)

    # ---- 非流式：替换响应中的 model 名后返回 ----
    if not stream:
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=timeout)
        except requests.RequestException as e:
            log.error("上游请求失败: %s", e)
            return {"error": f"upstream connection failed: {e}"}, 502

        if resp.status_code >= 400:
            try:
                return resp.json(), resp.status_code
            except json.JSONDecodeError:
                return {"error": resp.text[:500]}, resp.status_code

        data = resp.json()
        # 把上游模型名换回 Copilot 请求的名字，避免 Copilot 显示不一致
        data["model"] = model_name
        return data

    # ---- 流式：透传 SSE，逐行替换 model 名 ----
    try:
        resp = requests.post(
            url, headers=headers, json=body,
            stream=True, timeout=timeout,
        )
    except requests.RequestException as e:
        log.error("上游连接失败: %s", e)
        return {"error": f"upstream connection failed: {e}"}, 502

    if resp.status_code >= 400:
        try:
            err_body = resp.json()
        except json.JSONDecodeError:
            err_body = {"error": resp.text[:500]}
        resp.close()
        return err_body, resp.status_code

    def generate():
        try:
            with resp:
                for line in resp.iter_lines(decode_unicode=True):
                    if not line:
                        continue

                    if not line.startswith("data: "):
                        yield (line + "\n").encode("utf-8")
                        continue

                    data_str = line[6:]
                    if data_str == "[DONE]":
                        yield "data: [DONE]\n\n".encode("utf-8")
                        break

                    try:
                        chunk = json.loads(data_str)
                        chunk["model"] = model_name  # 换回 ollama_name
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8")
                    except json.JSONDecodeError:
                        yield (line + "\n").encode("utf-8")
        except requests.RequestException as e:
            log.error("流式传输中断: %s", e)

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        status=resp.status_code,
    )


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Ollama 原生对话端点——Ollama ↔ OpenAI 双向格式转换。

    VS2026 Copilot 实际走的是 /v1/chat/completions（OpenAI 透传），
    此端点保留给使用 Ollama 原生协议的客户端。

    支持流式/非流式、tools（Agent 模式）、images（视觉）。
    """
    body = request.get_json(silent=True) or {}
    model_name = body.get("model", "")

    model_cfg = find_model(model_name)
    if not model_cfg:
        return {"error": f"unknown model '{model_name}'"}, 404

    base_url = model_cfg["base_url"].rstrip("/")
    api_key = model_cfg["api_key"]
    timeout = server_config()["timeout"]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload, stream = build_openai_payload(model_cfg, body)
    url = f"{base_url}/chat/completions"

    tools_count = len(body.get("tools", []))
    log.info("/api/chat model=%s stream=%s tools=%s", model_name, stream, tools_count)

    # ================================================================
    # 非流式
    # ================================================================
    if not stream:
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as e:
            log.error("上游请求失败: %s", e)
            return {"error": f"upstream connection failed: {e}"}, 502

        if resp.status_code >= 400:
            try:
                return resp.json(), resp.status_code
            except json.JSONDecodeError:
                return {"error": resp.text[:500]}, resp.status_code

        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message", {})

        result = make_ollama_chunk(
            model_name,
            content=msg.get("content") or "",
            done=True,
            done_reason=normalize_finish_reason(choice.get("finish_reason")),
            tool_calls=openai_tool_calls_to_ollama(msg.get("tool_calls")),
            usage=data.get("usage"),
        )
        return result

    # ================================================================
    # 流式：OpenAI SSE → Ollama NDJSON
    # ================================================================
    try:
        resp = requests.post(
            url, headers=headers, json=payload,
            stream=True, timeout=timeout,
        )
    except requests.RequestException as e:
        log.error("上游连接失败: %s", e)
        return {"error": f"upstream connection failed: {e}"}, 502

    if resp.status_code >= 400:
        try:
            err_body = resp.json()
        except json.JSONDecodeError:
            err_body = {"error": resp.text[:500]}
        resp.close()
        return err_body, resp.status_code

    def generate():
        tc_acc = {}  # index → {id, name, arguments, index}

        try:
            with resp:
                for line in resp.iter_lines():
                    if not line:
                        continue

                    line = line.decode("utf-8")
                    if not line.startswith("data: "):
                        continue

                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue

                    delta = choices[0].get("delta", {}) or {}
                    finish_reason = choices[0].get("finish_reason")

                    # --- 累积 tool_calls 增量分片 ---
                    for tc in delta.get("tool_calls", []):
                        idx = tc.get("index", 0)
                        slot = tc_acc.setdefault(
                            idx,
                            {"id": "", "name": "", "arguments": "", "index": idx},
                        )
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]

                    # --- 文本增量 ---
                    content = delta.get("content") or ""
                    if content:
                        yield json.dumps(
                            make_ollama_chunk(model_name, content=content),
                            ensure_ascii=False,
                        ) + "\n"

                    # --- 流结束：输出最终 done 块 ---
                    if finish_reason is not None:
                        final_tc = None
                        if tc_acc:
                            final_tc = assembled_tool_calls_from_acc(tc_acc)

                        final = make_ollama_chunk(
                            model_name,
                            content="",
                            done=True,
                            done_reason=normalize_finish_reason(finish_reason),
                            tool_calls=final_tc,
                        )
                        yield json.dumps(final, ensure_ascii=False) + "\n"

        except requests.RequestException as e:
            log.error("流式传输中断: %s", e)
            yield json.dumps(
                {"error": f"stream error: {e}"}, ensure_ascii=False,
            ) + "\n"

    return Response(stream_with_context(generate()), mimetype="application/x-ndjson")

# ===================================================================
# 入口
# ===================================================================


def parse_args():
    p = argparse.ArgumentParser(
        description="OllamaProxy — 把自己伪装成 Ollama，转发到云端大模型 API",
    )
    p.add_argument(
        "-c", "--config", default=None,
        help=f"配置文件路径（默认: {DEFAULT_CONFIG_PATH}）",
    )
    return p.parse_args()


def main():
    global CONFIG

    args = parse_args()

    # 配置路径优先级：命令行 > 环境变量 > 默认路径
    config_path = (
        args.config
        or os.environ.get("OLLAMA_PROXY_CONFIG")
        or DEFAULT_CONFIG_PATH
    )

    print(f"加载配置: {config_path}")
    CONFIG = load_config(config_path)

    srv = server_config()
    models_list = [m["ollama_name"] for m in CONFIG["models"]]

    print("=" * 50)
    print(" OllamaProxy v1.0.0")
    print(f" 配置文件 : {config_path}")
    print(f" 监听地址 : http://{srv['host']}:{srv['port']}")
    print(f" 上游超时 : {srv['timeout']}s")
    print(f" 暴露模型 : {', '.join(models_list)}")
    print("=" * 50)

    app.run(host=srv["host"], port=srv["port"], debug=False, threaded=True)


if __name__ == "__main__":
    main()