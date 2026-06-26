"""
统一 AI 调用客户端 — 多提供商跨平台降级

降级链：ModelScope (Qwen3/DeepSeek) → SenseNova (deepseek-v4-flash, 仅 text/report)
  vision 跳过 SenseNova: ModelScope (VL/397B/122B/8B)

三类任务使用不同模型：
  - text: JSON 解析/预测（极速，结构化输出）
  - vision: 图像识别（准确，视觉专用）
  - report: 报告分析（长上下文，专业推理）

应用内置 API Key，用户无需自行配置。
自动检测可用的 API Key，按优先级依次尝试。
"""

import os
import base64
import httpx
from dotenv import load_dotenv
import settings

load_dotenv()

# API Keys
MODELSCOPE_API_KEY = os.getenv("MODELSCOPE_API_KEY")
SENSENOVA_API_KEY = os.getenv("SENSENOVA_API_KEY")

AI_AVAILABLE = bool(MODELSCOPE_API_KEY or SENSENOVA_API_KEY)

# 启动日志
_providers = []
if MODELSCOPE_API_KEY:
    _providers.append('ModelScope')
if SENSENOVA_API_KEY:
    _providers.append('SenseNova')

if len(_providers) > 1:
    print(f"[AI] {' + '.join(_providers)} 多提供商就绪（跨提供商降级）")
elif _providers:  # pragma: no cover
    print(f"[AI] 使用 {_providers[0]} 作为 AI 提供商")
else:  # pragma: no cover
    print("[AI] 未配置 AI 提供商，AI 功能将不可用")


def _call_openai_compatible(api_key, base_url, model, prompt, images_data=None, mime_type=None, provider='', extra_body=None):
    """通用 OpenAI 兼容接口调用（ModelScope / SenseNova 共用）"""
    from openai import OpenAI

    is_cn_endpoint = '.cn' in base_url
    http_client = httpx.Client(trust_env=False) if is_cn_endpoint else None

    client = OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
    has_images = images_data and len(images_data) > 0

    if has_images:
        content_parts = [{"type": "text", "text": prompt}]
        for img in images_data:
            b64_str = base64.b64encode(img).decode('utf-8')
            mt = mime_type or 'image/jpeg'
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mt};base64,{b64_str}"}
            })
        messages = [{"role": "user", "content": content_parts}]
    else:
        messages = [{"role": "user", "content": prompt}]

    kwargs = dict(model=model, messages=messages, temperature=0.7)
    if extra_body:
        kwargs['extra_body'] = extra_body
    response = client.chat.completions.create(**kwargs)
    print(f"[AI] ✓ {provider} {model} 响应成功")
    return response.choices[0].message.content


def _try_provider(api_key, base_url, models_config, has_images, prompt, images_data, mime_type, provider, task_type=None):
    """尝试单个 OpenAI 兼容提供商的模型链，返回 (result, last_error)

    task_type: 'text'(JSON解析), 'vision'(图像识别), 'report'(报告分析)
               None 时根据 has_images 自动选择 text/vision
    """
    if task_type and task_type in models_config:
        model_list = models_config[task_type]
    else:
        model_list = models_config['vision'] if has_images else models_config['text']

    effective_task = task_type or ('vision' if has_images else 'text')
    extra_body = models_config.get('extra_body') if effective_task == 'text' else None
    last_error = None

    for model in model_list:
        try:
            result = _call_openai_compatible(
                api_key, base_url, model,
                prompt, images_data, mime_type, provider=provider,
                extra_body=extra_body)
            return result, None
        except Exception as e:
            last_error = e
            print(f"⚠ {provider} {model} 调用失败: {str(e)[:100]}")
            continue

    return None, last_error


def call_ai(prompt, images_data=None, mime_type=None, task_type=None):
    """
    统一 AI 调用接口，支持跨提供商降级。

    降级链:
      ModelScope (Qwen3/DeepSeek) → SenseNova (deepseek-v4-flash, 仅 text/report)
      vision 跳过 SenseNova: 仅 ModelScope

    Args:
        task_type: 任务类型，影响模型选择
            - 'text': JSON解析/预测（极速，结构化输出）
            - 'vision': 图像识别（准确，视觉专用）
            - 'report': 报告分析（长上下文，专业推理）
            - None: 根据 images_data 自动选择 text/vision
    """
    if not AI_AVAILABLE:
        raise Exception("AI 服务未配置，请设置 MODELSCOPE_API_KEY 或 SENSENOVA_API_KEY")

    has_images = images_data and len(images_data) > 0
    if has_images and not task_type:
        task_type = 'vision'
    last_error = None

    # === 阶段1: 尝试 ModelScope 模型链 ===
    if MODELSCOPE_API_KEY:
        result, err = _try_provider(
            MODELSCOPE_API_KEY, settings.MODELSCOPE_BASE_URL, settings.MODELSCOPE_MODELS,
            has_images, prompt, images_data, mime_type, 'ModelScope', task_type=task_type)
        if result is not None:
            return result
        if err:
            last_error = err
            print("⚠ ModelScope 全部不可用，降级到 SenseNova...")

    # === 阶段2: 尝试 SenseNova 模型链（vision 不走 SenseNova）===
    if SENSENOVA_API_KEY and task_type != 'vision':
        result, err = _try_provider(
            SENSENOVA_API_KEY, settings.SENSENOVA_BASE_URL, settings.SENSENOVA_MODELS,
            has_images, prompt, images_data, mime_type, 'SenseNova', task_type=task_type)
        if result is not None:
            return result
        if err:
            last_error = err

    # 全部失败
    raise last_error if last_error else Exception("所有 AI 模型均不可用")


# ========== 健康助手流式聊天 ==========

SENSENOVA_CHAT_BASE_URL = settings.SENSENOVA_BASE_URL
SENSENOVA_CHAT_MODEL = "deepseek-v4-flash"
MODELSCOPE_CHAT_MODEL = settings.MODELSCOPE_MODELS['chat'][0]
CHAT_AVAILABLE = bool(SENSENOVA_API_KEY or MODELSCOPE_API_KEY)

if CHAT_AVAILABLE:
    print(f"[AI] 健康助手就绪（SenseNova {SENSENOVA_CHAT_MODEL}）")


def _stream_chat(client, model, messages, extra_body=None):
    """通用 OpenAI 兼容流式聊天调用"""
    kwargs = dict(model=model, messages=messages, stream=True)
    if extra_body:
        kwargs["extra_body"] = extra_body
    response = client.chat.completions.create(**kwargs)
    for chunk in response:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def call_chat_stream(messages):
    """流式聊天调用 — SenseNova 优先，ModelScope fallback。"""
    from openai import OpenAI

    def _client_for(base_url, api_key):
        is_cn = ".cn" in base_url
        http_client = httpx.Client(trust_env=False) if is_cn else None
        return OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)

    # 1. 优先 SenseNova
    if SENSENOVA_API_KEY:
        try:
            client = _client_for(SENSENOVA_CHAT_BASE_URL, SENSENOVA_API_KEY)
            yield from _stream_chat(client, SENSENOVA_CHAT_MODEL, messages)
            return
        except Exception as e:
            print(f"⚠ SenseNova 健康助手调用失败: {str(e)[:100]}")
            if not MODELSCOPE_API_KEY:
                raise

    # 2. Fallback 到 ModelScope
    if MODELSCOPE_API_KEY:
        try:
            client = _client_for(settings.MODELSCOPE_BASE_URL, MODELSCOPE_API_KEY)
            yield from _stream_chat(client, MODELSCOPE_CHAT_MODEL, messages)
            return
        except Exception as e:
            print(f"⚠ ModelScope 健康助手调用失败: {str(e)[:100]}")
            raise

    raise Exception("未配置聊天 AI 服务，请设置 SENSENOVA_API_KEY 或 MODELSCOPE_API_KEY")
