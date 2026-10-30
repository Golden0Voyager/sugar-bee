"""
统一 AI 调用客户端 — 多供应商降级

四个任务共用同一条降级顺序(见 settings.AI_PROVIDER_ORDER):
    端砚 → SenseNova → Agnes → ModelScope

  - text   : JSON 解析 / 血糖预测(高频,要快)
  - vision : 截图识别(读图)
  - report : 报告分析(长上下文)
  - chat   : 健康助手(流式)

每家供应商的模型清单、extra_body 写法、超时都在 settings.AI_PROVIDERS 里配置。
应用内置 API Key,用户无需自行配置;自动检测可用的 Key,按顺序依次尝试。

两个容易静默失败的点,改动时务必保留:
  1. 思考型模型的输出会写进 reasoning_content,content 是空字符串 —— 必须当失败往下走,不能当成功返回;
  2. 流式对话里如果已经开始吐字,不能再换一家重头来,否则用户会看到重复内容。
"""

import base64
import os
import time

import httpx
from dotenv import load_dotenv

import settings

load_dotenv()

# API Keys —— 模块级常量,测试通过 patch 这些名字来构造无 Key 场景
INTERN_API_KEY = os.getenv("INTERN_API_KEY")
SENSENOVA_API_KEY = os.getenv("SENSENOVA_API_KEY")
AGNES_API_KEY = os.getenv("AGNES_API_KEY")
MODELSCOPE_API_KEY = os.getenv("MODELSCOPE_API_KEY")

# 供应商 id → 存放 Key 的模块属性名
_PROVIDER_KEY_ATTRS = {
    provider_id: config['api_key_env'] for provider_id, config in settings.AI_PROVIDERS.items()
}


def _provider_key(provider_id):
    """取供应商的 API Key。

    读的是模块属性而不是导入时的快照,这样测试 patch ai_client.MODELSCOPE_API_KEY 才能生效。
    """
    return globals().get(_PROVIDER_KEY_ATTRS[provider_id])


AI_AVAILABLE = bool(INTERN_API_KEY or SENSENOVA_API_KEY or AGNES_API_KEY or MODELSCOPE_API_KEY)

# 启动日志
_ready_providers = [
    settings.AI_PROVIDERS[pid]['name']
    for pid in settings.AI_PROVIDER_ORDER
    if _provider_key(pid)
]

if _ready_providers:
    print(f"[AI] 降级链: {' → '.join(_ready_providers)}")
else:  # pragma: no cover
    print("[AI] 未配置 AI 提供商,AI 功能将不可用")


# 限流 / 超时 / 连接失败重试一次再换下一家。只对这类瞬时错误退避,
# 模型返回空内容这种「确定性失败」立即换家,不浪费等待。
_MAX_ATTEMPTS = 2
_RETRY_BASE_DELAY = 3
_RETRYABLE_NAMES = ('ratelimit', 'timeout', 'apiconnection', 'internalserver', 'serviceunavailable')


def _is_retryable(exc):
    if any(hint in type(exc).__name__.lower() for hint in _RETRYABLE_NAMES):
        return True
    text = str(exc).lower()
    return any(hint in text for hint in ('429', 'rate limit', 'timed out', 'timeout'))


def _provider_order(task_type):
    """按任务取供应商顺序;没有单独配置的任务沿用默认顺序。"""
    return settings.AI_TASK_PROVIDER_ORDER.get(task_type, settings.AI_PROVIDER_ORDER)


def _call_openai_compatible(api_key, base_url, model, prompt, images_data=None, mime_type=None,
                            provider='', extra_body=None, timeout=None):
    """通用 OpenAI 兼容接口调用(四家供应商共用)"""
    from openai import OpenAI

    is_cn_endpoint = '.cn' in base_url
    http_client = httpx.Client(trust_env=False) if is_cn_endpoint else None

    kwargs = {"api_key": api_key, "base_url": base_url, "http_client": http_client, "max_retries": 1}
    if timeout:
        kwargs["timeout"] = timeout
    client = OpenAI(**kwargs)

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

    call_kwargs = {"model": model, "messages": messages, "temperature": 0.7}
    if extra_body:
        call_kwargs['extra_body'] = extra_body
    response = client.chat.completions.create(**call_kwargs)
    print(f"[AI] ✓ {provider} {model} 响应成功")
    return response.choices[0].message.content


def _try_provider(api_key, base_url, models_config, has_images, prompt, images_data, mime_type,
                  provider, task_type=None, timeout=None):
    """尝试单个 OpenAI 兼容供应商的模型链,返回 (result, last_error)

    task_type: 'text' / 'vision' / 'report';None 时根据 has_images 自动选择 text 或 vision
    """
    if task_type and task_type in models_config:
        model_list = models_config[task_type]
    else:
        model_list = models_config['vision'] if has_images else models_config['text']

    extra_body = models_config.get('extra_body')
    last_error = None

    for model in model_list:
        for attempt in range(_MAX_ATTEMPTS):
            try:
                result = _call_openai_compatible(
                    api_key, base_url, model,
                    prompt, images_data, mime_type, provider=provider,
                    extra_body=extra_body, timeout=timeout)
                if not result or not str(result).strip():
                    # 思考型模型会把内容全写进 reasoning_content,这里拿到空串。
                    # 必须当失败处理并继续降级,否则会静默返回空结果。
                    raise ValueError(f"{provider} {model} 返回空内容(reasoning_content?)")
                return result, None
            except Exception as e:
                last_error = e
                if attempt + 1 < _MAX_ATTEMPTS and _is_retryable(e):
                    delay = _RETRY_BASE_DELAY * (attempt + 1)
                    print(f"⚠ {provider} {model} 限流/超时,{delay}s 后重试: {str(e)[:80]}")
                    time.sleep(delay)
                    continue
                print(f"⚠ {provider} {model} 调用失败: {str(e)[:100]}")
                break

    return None, last_error


def call_ai(prompt, images_data=None, mime_type=None, task_type=None):
    """
    统一 AI 调用接口,按 settings.AI_PROVIDER_ORDER 跨供应商降级。

    Args:
        task_type: 任务类型,影响各家挑哪个模型
            - 'text'  : JSON 解析 / 血糖预测
            - 'vision': 截图识别
            - 'report': 报告分析
            - None    : 有图片按 vision,否则按 text
    """
    if not AI_AVAILABLE:
        raise Exception("AI 服务未配置,请设置任一 AI 供应商的 API Key")

    has_images = images_data and len(images_data) > 0
    if not task_type:
        task_type = 'vision' if has_images else 'text'
    last_error = None

    for provider_id in _provider_order(task_type):
        config = settings.AI_PROVIDERS[provider_id]
        api_key = _provider_key(provider_id)
        if not api_key:
            continue
        # 这家没给该任务配模型(例如纯文本供应商没有 vision 模型)就跳过
        if not config['models'].get(task_type):
            continue

        models_config = dict(config['models'])
        if config.get('extra_body'):
            models_config['extra_body'] = config['extra_body']

        result, err = _try_provider(
            api_key, config['base_url'], models_config,
            has_images, prompt, images_data, mime_type, provider=config['name'],
            task_type=task_type, timeout=config.get('timeout'))
        if result is not None:
            return result
        if err:
            last_error = err

    raise last_error if last_error else Exception("所有 AI 模型均不可用")


# ========== 健康助手流式聊天 ==========

CHAT_AVAILABLE = bool(INTERN_API_KEY or SENSENOVA_API_KEY or AGNES_API_KEY or MODELSCOPE_API_KEY)


def _client_for(base_url, api_key, timeout=None):
    from openai import OpenAI

    is_cn = ".cn" in base_url
    http_client = httpx.Client(trust_env=False) if is_cn else None
    kwargs = {"api_key": api_key, "base_url": base_url, "http_client": http_client, "max_retries": 1}
    if timeout:
        kwargs["timeout"] = timeout
    return OpenAI(**kwargs)


def _stream_chat(client, model, messages, extra_body=None):
    """通用 OpenAI 兼容流式聊天调用"""
    kwargs = {"model": model, "messages": messages, "stream": True}
    if extra_body:
        kwargs["extra_body"] = extra_body
    response = client.chat.completions.create(**kwargs)
    for chunk in response:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def call_chat_stream(messages):
    """流式聊天调用 — 与 call_ai 走同一条供应商顺序,取各家的 chat 模型。"""
    last_error = None

    for provider_id in settings.AI_PROVIDER_ORDER:
        config = settings.AI_PROVIDERS[provider_id]
        api_key = _provider_key(provider_id)
        if not api_key:
            continue

        for model in config['models'].get('chat') or []:
            yielded = False
            for attempt in range(_MAX_ATTEMPTS):
                try:
                    client = _client_for(config['base_url'], api_key, config.get('timeout'))
                    for chunk in _stream_chat(client, model, messages, config.get('extra_body')):
                        yielded = True
                        yield chunk
                except Exception as e:
                    print(f"⚠ {config['name']} {model} 健康助手调用失败: {str(e)[:100]}")
                    last_error = e
                    if yielded:
                        # 已经吐字了,换一家重头来会让用户看到重复内容,只能就地报错
                        raise
                    if attempt + 1 < _MAX_ATTEMPTS and _is_retryable(e):
                        delay = _RETRY_BASE_DELAY * (attempt + 1)
                        print(f"⚠ {config['name']} {model} 限流/超时,{delay}s 后重试")
                        time.sleep(delay)
                        continue
                break

            if yielded:
                return
            # 流正常结束但一个字都没吐(思考型模型只流 reasoning_content),继续换下一家
            last_error = Exception(f"{config['name']} {model} 未返回任何内容")
            print(f"⚠ {config['name']} {model} 未返回任何内容,继续降级")

    raise last_error if last_error else Exception("未配置聊天 AI 服务,请设置任一 AI 供应商的 API Key")
