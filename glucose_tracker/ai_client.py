"""
统一 AI 调用客户端 — 支持 Gemini 和 ZhipuAI (智谱AI) 跨提供商降级

降级链：gemini-3-flash-preview → gemini-2.5-flash → zhipuai (glm-4.7-flash / glm-4.6v-flash)

自动检测可用的 API Key：
- GEMINI_API_KEY: Gemini 系列模型（海外）
- ZHIPUAI_API_KEY: ZhipuAI 系列模型（国内可用，OpenAI 兼容接口）
- 两者都有时，先 Gemini 后 ZhipuAI 跨提供商降级
"""

import os
import base64
from dotenv import load_dotenv
import settings

load_dotenv()

# API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ZHIPUAI_API_KEY = os.getenv("ZHIPUAI_API_KEY")

AI_AVAILABLE = bool(GEMINI_API_KEY or ZHIPUAI_API_KEY)

if GEMINI_API_KEY and ZHIPUAI_API_KEY:
    print(f"[AI] Gemini + ZhipuAI 双提供商就绪（跨提供商降级）")
elif GEMINI_API_KEY:
    print(f"[AI] 使用 GEMINI 作为 AI 提供商")
elif ZHIPUAI_API_KEY:
    print(f"[AI] 使用 ZHIPUAI 作为 AI 提供商")
else:
    print("[AI] 未配置 AI 提供商，AI 功能将不可用")


def _call_gemini_model(model, prompt, images_data=None, mime_type=None):
    """调用单个 Gemini 模型"""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)
    contents = [prompt]
    if images_data:
        for img in images_data:
            contents.append(types.Part.from_bytes(data=img, mime_type=mime_type or 'image/jpeg'))

    response = client.models.generate_content(model=model, contents=contents)
    print(f"[AI] ✓ Gemini {model} 响应成功")
    return response.text


def _call_zhipuai_model(model, prompt, images_data=None, mime_type=None):
    """调用单个 ZhipuAI 模型"""
    from openai import OpenAI

    client = OpenAI(api_key=ZHIPUAI_API_KEY, base_url=settings.ZHIPUAI_BASE_URL)
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

    response = client.chat.completions.create(model=model, messages=messages, temperature=0.7)
    print(f"[AI] ✓ ZhipuAI {model} 响应成功")
    return response.choices[0].message.content


def call_ai(prompt, images_data=None, mime_type=None):
    """
    统一 AI 调用接口，支持跨提供商降级。

    降级链:
      gemini-3-flash-preview → gemini-2.5-flash → zhipuai (glm-4.7-flash 或 glm-4.6v-flash)

    图片请求自动选择视觉模型（ZhipuAI 时用 glm-4.6v-flash）。
    """
    if not AI_AVAILABLE:
        raise Exception("AI 服务未配置，请设置 GEMINI_API_KEY 或 ZHIPUAI_API_KEY")

    has_images = images_data and len(images_data) > 0
    last_error = None

    # === 阶段1: 尝试 Gemini 模型链 ===
    if GEMINI_API_KEY:
        for model in settings.GEMINI_MODELS:
            try:
                return _call_gemini_model(model, prompt, images_data, mime_type)
            except Exception as e:
                error_str = str(e)
                last_error = e
                if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str:
                    print(f"⚠ Gemini {model} 配额耗尽，尝试降级...")
                elif '404' in error_str or 'NOT_FOUND' in error_str:
                    print(f"⚠ Gemini {model} 模型不可用，尝试降级...")
                else:
                    print(f"⚠ Gemini {model} 调用失败: {error_str[:100]}，尝试降级...")
                continue

        # Gemini 全部不可用，尝试 ZhipuAI
        if ZHIPUAI_API_KEY:
            print(f"⚠ Gemini 全部不可用，跨提供商降级到 ZhipuAI...")

    # === 阶段2: 尝试 ZhipuAI 模型 ===
    if ZHIPUAI_API_KEY:
        # 根据是否有图片选择对应模型
        if has_images:
            model_list = settings.ZHIPUAI_MODELS['vision']
        else:
            model_list = settings.ZHIPUAI_MODELS['text']

        for model in model_list:
            try:
                return _call_zhipuai_model(model, prompt, images_data, mime_type)
            except Exception as e:
                error_str = str(e)
                last_error = e
                print(f"⚠ ZhipuAI {model} 调用失败: {error_str[:100]}")
                continue

    # 全部失败
    raise last_error if last_error else Exception("所有 AI 模型均不可用")
