"""ai_client.py 测试 — mock OpenAI/Gemini 测试降级链、_try_provider、call_ai"""
from unittest.mock import MagicMock, patch

import pytest


class TestAiClientConfig:
    """模块级配置常量测试"""

    def test_ai_available_detection(self):
        # ai_client already imported at module level; test the concept
        from ai_client import AI_AVAILABLE, GEMINI_API_KEY, MODELSCOPE_API_KEY, SENSENOVA_API_KEY, VOLC_API_KEY
        expected = bool(MODELSCOPE_API_KEY or SENSENOVA_API_KEY or VOLC_API_KEY or GEMINI_API_KEY)
        assert expected == AI_AVAILABLE

    def test_chat_available(self):
        from ai_client import CHAT_AVAILABLE, SENSENOVA_API_KEY
        assert bool(SENSENOVA_API_KEY) == CHAT_AVAILABLE

    def test_chat_model_configured(self):
        from ai_client import SENSENOVA_CHAT_BASE_URL, SENSENOVA_CHAT_MODEL
        assert SENSENOVA_CHAT_MODEL == "deepseek-v4-flash"
        assert "sensenova.cn" in SENSENOVA_CHAT_BASE_URL


class TestTryProvider:
    """_try_provider() 模型选择与降级测试"""

    @patch('ai_client._call_openai_compatible')
    def test_selects_text_models_when_no_images(self, mock_call):
        from ai_client import _try_provider
        mock_call.return_value = "success"
        models_config = {
            'text': ['text-model-1', 'text-model-2'],
            'vision': ['vision-model-1'],
        }

        result, err = _try_provider(
            'fake-key', 'https://api.test.com', models_config,
            has_images=False, prompt='hello', images_data=None,
            mime_type=None, provider='Test'
        )
        assert result == "success"
        assert err is None
        # Should have called with first text model
        called_model = mock_call.call_args[0][2]
        assert called_model == 'text-model-1'

    @patch('ai_client._call_openai_compatible')
    def test_selects_vision_models_when_has_images(self, mock_call):
        from ai_client import _try_provider
        mock_call.return_value = "success"
        models_config = {
            'text': ['text-model'],
            'vision': ['vision-model-1'],
        }

        result, err = _try_provider(
            'fake-key', 'https://api.test.com', models_config,
            has_images=True, prompt='hello', images_data=[b'fake'],
            mime_type='image/jpeg', provider='Test'
        )
        assert result == "success"
        called_model = mock_call.call_args[0][2]
        assert called_model == 'vision-model-1'

    @patch('ai_client._call_openai_compatible')
    def test_selects_by_task_type(self, mock_call):
        from ai_client import _try_provider
        mock_call.return_value = "success"
        models_config = {
            'text': ['text-model'],
            'vision': ['vision-model'],
            'report': ['report-model'],
        }

        result, err = _try_provider(
            'fake-key', 'https://api.test.com', models_config,
            has_images=False, prompt='hello', images_data=None,
            mime_type=None, provider='Test', task_type='report'
        )
        assert result == "success"
        called_model = mock_call.call_args[0][2]
        assert called_model == 'report-model'

    @patch('ai_client._call_openai_compatible')
    def test_falls_through_model_chain(self, mock_call):
        from ai_client import _try_provider
        mock_call.side_effect = [Exception("fail1"), Exception("fail2"), "success"]

        models_config = {
            'text': ['m1', 'm2', 'm3'],
            'vision': [],
        }
        result, err = _try_provider(
            'fake-key', 'https://api.test.com', models_config,
            has_images=False, prompt='hello', images_data=None,
            mime_type=None, provider='Test'
        )
        assert result == "success"
        assert mock_call.call_count == 3

    @patch('ai_client._call_openai_compatible')
    def test_all_models_fail(self, mock_call):
        from ai_client import _try_provider
        mock_call.side_effect = Exception("all fail")

        models_config = {
            'text': ['m1', 'm2'],
            'vision': [],
        }
        result, err = _try_provider(
            'fake-key', 'https://api.test.com', models_config,
            has_images=False, prompt='hello', images_data=None,
            mime_type=None, provider='Test'
        )
        assert result is None
        assert err is not None

    @patch('ai_client._call_openai_compatible')
    def test_extra_body_for_text_tasks(self, mock_call):
        from ai_client import _try_provider
        mock_call.return_value = "success"
        models_config = {
            'text': ['m1'],
            'extra_body': {'enable_thinking': False},
        }

        _try_provider(
            'fake-key', 'https://api.test.com', models_config,
            has_images=False, prompt='hello', images_data=None,
            mime_type=None, provider='Test', task_type='text'
        )
        # extra_body should be passed
        assert mock_call.call_args[1].get('extra_body') == {'enable_thinking': False}


class TestCallAi:
    """call_ai() 降级链测试"""

    @patch('ai_client.AI_AVAILABLE', False)
    def test_raises_when_no_ai_available(self):
        from ai_client import call_ai
        with pytest.raises(Exception, match='AI 服务未配置'):
            call_ai('test prompt')

    @patch('ai_client.AI_AVAILABLE', True)
    @patch('ai_client.GEMINI_API_KEY', None)
    @patch('ai_client.VOLC_API_KEY', None)
    @patch('ai_client.MODELSCOPE_API_KEY', 'fake-key')
    @patch('ai_client._try_provider')
    def test_modelscope_success(self, mock_try):
        from ai_client import call_ai
        mock_try.return_value = ("modelscope result", None)
        result = call_ai('test prompt')
        assert result == "modelscope result"

    @patch('ai_client.GEMINI_API_KEY', None)
    @patch('ai_client.VOLC_API_KEY', None)
    @patch('ai_client.MODELSCOPE_API_KEY', 'fake-key')
    @patch('ai_client._try_provider')
    def test_modelscope_fail_fallback(self, mock_try):
        from ai_client import call_ai
        mock_try.return_value = (None, Exception("fail"))
        with pytest.raises(Exception):
            call_ai('test prompt')

    @patch('ai_client.AI_AVAILABLE', True)
    @patch('ai_client.GEMINI_API_KEY', None)
    @patch('ai_client.VOLC_API_KEY', None)
    @patch('ai_client.MODELSCOPE_API_KEY', 'fake-key')
    @patch('ai_client._try_provider')
    def test_has_images_forces_vision(self, mock_try):
        from ai_client import call_ai
        mock_try.return_value = ("result", None)
        call_ai('test', images_data=[b'fake'])
        # task_type should be 'vision' when images provided
        assert mock_try.call_args[1].get('task_type') == 'vision'

    @patch('ai_client.AI_AVAILABLE', True)
    @patch('ai_client.GEMINI_API_KEY', 'gemini-key')
    @patch('ai_client.VOLC_API_KEY', 'volc-key')
    @patch('ai_client.MODELSCOPE_API_KEY', 'ms-key')
    @patch('ai_client._try_provider')
    def test_volc_engine_success(self, mock_try):
        """L167: ModelScope 失败 → 火山引擎成功返回"""
        from ai_client import call_ai
        # ModelScope fails, Volc succeeds
        mock_try.side_effect = [
            (None, Exception("ModelScope failed")),  # Phase 1: ModelScope
            ("volc result", None),                   # Phase 2: Volc
        ]

        result = call_ai('test prompt')
        assert result == "volc result"
        assert mock_try.call_count == 2


class TestCallChatStream:
    """call_chat_stream() 流式聊天测试 — SenseNova 优先，ModelScope fallback"""

    @patch('ai_client.SENSENOVA_API_KEY', 'fake-key')
    @patch('openai.OpenAI')
    def test_yields_chunks(self, mock_openai_cls):
        from ai_client import call_chat_stream
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta.content = "Hello"

        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta.content = " World"

        chunk_done = MagicMock()
        chunk_done.choices = [MagicMock()]
        chunk_done.choices[0].delta.content = None

        mock_client.chat.completions.create.return_value = [chunk1, chunk2, chunk_done]

        result = list(call_chat_stream([{"role": "user", "content": "hi"}]))
        assert result == ["Hello", " World"]
        # 应使用 SenseNova 主模型
        assert mock_client.chat.completions.create.call_args[1]['model'] == 'deepseek-v4-flash'

    @patch('ai_client.SENSENOVA_API_KEY', None)
    @patch('ai_client.MODELSCOPE_API_KEY', 'fake-key')
    @patch('openai.OpenAI')
    def test_modelscope_fallback_when_sensenova_missing(self, mock_openai_cls):
        """未配置 SenseNova 时，直接使用 ModelScope fallback"""
        from ai_client import call_chat_stream
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = "fallback"
        mock_client.chat.completions.create.return_value = [chunk]

        result = list(call_chat_stream([{"role": "user", "content": "hi"}]))
        assert result == ["fallback"]
        assert mock_client.chat.completions.create.call_args[1]['model'] == 'deepseek-ai/DeepSeek-V4-Pro'

    @patch('ai_client.SENSENOVA_API_KEY', 'fake-key')
    @patch('ai_client.MODELSCOPE_API_KEY', 'fake-key')
    @patch('openai.OpenAI')
    def test_modelscope_fallback_when_sensenova_fails(self, mock_openai_cls):
        """SenseNova 调用失败时降级到 ModelScope"""
        from ai_client import call_chat_stream

        fallback_client = MagicMock()
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = "modelscope"
        fallback_client.chat.completions.create.return_value = [chunk]

        def side_effect(*args, **kwargs):
            if mock_openai_cls.call_count == 1:
                raise Exception("SenseNova down")
            return fallback_client

        mock_openai_cls.side_effect = side_effect

        result = list(call_chat_stream([{"role": "user", "content": "hi"}]))
        assert result == ["modelscope"]
        assert fallback_client.chat.completions.create.call_args[1]['model'] == 'deepseek-ai/DeepSeek-V4-Pro'

    @patch('ai_client.SENSENOVA_API_KEY', 'fake-key')
    @patch('ai_client.MODELSCOPE_API_KEY', 'fake-key')
    @patch('openai.OpenAI')
    def test_all_chat_providers_fail(self, mock_openai_cls):
        """SenseNova 和 ModelScope 都失败时抛出异常"""
        from ai_client import call_chat_stream
        mock_openai_cls.side_effect = Exception("all down")

        with pytest.raises(Exception):
            list(call_chat_stream([{"role": "user", "content": "hi"}]))


class TestCallOpenaiCompatible:
    """_call_openai_compatible() 测试"""

    @patch('openai.OpenAI')
    @patch('ai_client.httpx.Client')
    def test_text_only_call(self, mock_httpx_client, mock_openai_cls):
        from ai_client import _call_openai_compatible
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        mock_choice = MagicMock()
        mock_choice.message.content = "response"
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[mock_choice]
        )

        result = _call_openai_compatible(
            'key', 'https://api.test.com', 'model-1',
            'hello', provider='Test'
        )
        assert result == "response"

    @patch('openai.OpenAI')
    @patch('ai_client.httpx.Client')
    def test_with_images(self, mock_httpx_client, mock_openai_cls):
        from ai_client import _call_openai_compatible
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        mock_choice = MagicMock()
        mock_choice.message.content = "image response"
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[mock_choice]
        )

        result = _call_openai_compatible(
            'key', 'https://api.test.com', 'model-v',
            'describe this', images_data=[b'\xff\xd8\xff'], mime_type='image/jpeg',
            provider='Test'
        )
        assert result == "image response"
        call_messages = mock_client.chat.completions.create.call_args[1]['messages']
        assert len(call_messages[0]['content']) == 2  # text + image

    @patch('openai.OpenAI')
    @patch('ai_client.httpx.Client')
    def test_cn_endpoint_no_proxy(self, mock_httpx_client, mock_openai_cls):
        from ai_client import _call_openai_compatible
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_httpx_instance = MagicMock()
        mock_httpx_client.return_value = mock_httpx_instance

        mock_choice = MagicMock()
        mock_choice.message.content = "ok"
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[mock_choice]
        )

        _call_openai_compatible(
            'key', 'https://api.volces.com/v1', 'model',
            'hello', provider='Test'
        )
        mock_httpx_client.assert_called_once_with(trust_env=False)

    @patch('openai.OpenAI')
    @patch('ai_client.httpx.Client')
    def test_non_cn_endpoint_default_client(self, mock_httpx_client, mock_openai_cls):
        from ai_client import _call_openai_compatible
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        mock_choice = MagicMock()
        mock_choice.message.content = "ok"
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[mock_choice]
        )

        _call_openai_compatible(
            'key', 'https://api.openai.com/v1', 'model',
            'hello', provider='Test'
        )
        # Non-CN endpoint → httpx not called, http_client=None passed to OpenAI
        assert mock_openai_cls.call_args[1].get('http_client') is None
        mock_httpx_client.assert_not_called()

    @patch('openai.OpenAI')
    @patch('ai_client.httpx.Client')
    def test_openai_compatible_with_extra_body(self, mock_httpx_client, mock_openai_cls):
        """L89: extra_body 正确转发到 create() 调用"""
        from ai_client import _call_openai_compatible
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        mock_choice = MagicMock()
        mock_choice.message.content = "extra_body response"
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[mock_choice]
        )

        _call_openai_compatible(
            'key', 'https://api.test.com/v1', 'model',
            'hello', provider='Test', extra_body={'enable_thinking': False}
        )
        # extra_body 应该被传递到 create 调用
        create_kwargs = mock_client.chat.completions.create.call_args[1]
        assert create_kwargs.get('extra_body') == {'enable_thinking': False}
"""
ai_client.py 最后覆盖冲刺 (87% → 100%)

未覆盖行:
  L41-44: _call_gemini_model client setup + contents with images
  L55-56: CN endpoint detection (httpx trust_env bypass)
  L168-170: call_ai Gemini fallback loop (if/for/try)
  L174-180: Gemini failure + all-failed raise
"""
from unittest.mock import patch

# ============================================================
# _call_gemini_model (L41-44, L54-58)
# ============================================================

class TestCallGeminiModel:
    """_call_gemini_model — 通过 sys.modules mock google.genai（google 是 namespace pkg）"""

    @patch('ai_client.GEMINI_API_KEY', 'fake-gemini-key')
    @patch('ai_client.genai.Client')
    def test_gemini_text_only(self, mock_client_cls):
        """纯文本调用 Gemini 模型"""
        from ai_client import _call_gemini_model
        mock_response = MagicMock()
        mock_response.text = "gemini response"
        mock_client_cls.return_value.models.generate_content.return_value = mock_response

        result = _call_gemini_model("gemini-model", "hello")
        assert result == "gemini response"
        mock_client_cls.assert_called_once_with(api_key='fake-gemini-key')

    @patch('ai_client.GEMINI_API_KEY', 'fake-gemini-key')
    @patch('ai_client.types.Part.from_bytes')
    @patch('ai_client.genai.Client')
    def test_gemini_with_images(self, mock_client_cls, mock_from_bytes):
        """带图片调用 Gemini 模型"""
        from ai_client import _call_gemini_model
        mock_response = MagicMock()
        mock_response.text = "图片分析结果"
        mock_client_cls.return_value.models.generate_content.return_value = mock_response
        mock_from_bytes.return_value = "part"

        result = _call_gemini_model(
            "gemini-model", "描述图片", images_data=[b'fake_img_data'],
            mime_type='image/png'
        )
        assert result == "图片分析结果"
        call_args = mock_client_cls.return_value.models.generate_content.call_args
        contents = call_args[1]['contents']
        assert len(contents) == 2
        assert contents[0] == "描述图片"
        mock_from_bytes.assert_called_once()

    @patch('ai_client.GEMINI_API_KEY', 'fake-gemini-key')
    @patch('ai_client.types.Part.from_bytes')
    @patch('ai_client.genai.Client')
    def test_gemini_client_setup_executes(self, mock_client_cls, mock_from_bytes):
        """L40-48: mock ai_client.genai 模块级引用，确保 coverage 可追踪"""
        from ai_client import _call_gemini_model

        mock_response = MagicMock()
        mock_response.text = "gemini direct"
        mock_client_cls.return_value.models.generate_content.return_value = mock_response
        mock_from_bytes.return_value = "image_part"

        # 纯文本：覆盖 L40-41
        result = _call_gemini_model("model-x", "hello")
        assert result == "gemini direct"
        mock_client_cls.assert_called_once_with(api_key='fake-gemini-key')

        # 带图片：覆盖 L42-44 (if images_data: for img in ...)
        result2 = _call_gemini_model(
            "model-x", "describe pic",
            images_data=[b'img1', b'img2'], mime_type='image/png'
        )
        assert result2 == "gemini direct"
        assert mock_from_bytes.call_count == 2


# ============================================================
# CN endpoint detection (L55-56)
# ============================================================

class TestCnEndpoint:
    """_call_openai_compatible CN 端点代理绕过"""

    @patch('openai.OpenAI')
    @patch('ai_client.httpx.Client')
    def test_cn_dot_cn_domain(self, mock_httpx, mock_openai):
        """.cn 域名 → http_client 携带 trust_env=False"""
        from ai_client import _call_openai_compatible
        mock_httpx.return_value = MagicMock()
        mock_openai.return_value.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="ok"))]
        )

        _call_openai_compatible(
            'key', 'https://api.someservice.cn/v1', 'm1',
            'hello', provider='Test'
        )
        mock_httpx.assert_called_once_with(trust_env=False)

    @patch('openai.OpenAI')
    @patch('ai_client.httpx.Client')
    def test_volces_domain(self, mock_httpx, mock_openai):
        """volces.com 域名 → http_client 携带 trust_env=False"""
        from ai_client import _call_openai_compatible
        mock_httpx.return_value = MagicMock()
        mock_openai.return_value.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="ok"))]
        )

        _call_openai_compatible(
            'key', 'https://api.volces.com/v1', 'm1',
            'hello', provider='Test'
        )
        mock_httpx.assert_called_once_with(trust_env=False)

    @patch('openai.OpenAI')
    @patch('ai_client.httpx.Client')
    def test_non_cn_domain_no_proxy_bypass(self, mock_httpx, mock_openai):
        """非 CN 域名 → http_client=None, 不调用 httpx.Client"""
        from ai_client import _call_openai_compatible
        mock_openai.return_value.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="ok"))]
        )

        _call_openai_compatible(
            'key', 'https://api.openai.com/v1', 'm1',
            'hello', provider='Test'
        )
        mock_httpx.assert_not_called()
        # http_client should be None
        assert mock_openai.call_args[1].get('http_client') is None


# ============================================================
# call_ai Gemini fallback (L168-180)
# ============================================================

class TestCallAiGeminiFallback:
    """call_ai Gemini直连降级 + 全失败"""

    @patch('ai_client.AI_AVAILABLE', True)
    @patch('ai_client.MODELSCOPE_API_KEY', 'ms-key')
    @patch('ai_client.VOLC_API_KEY', 'volc-key')
    @patch('ai_client.GEMINI_API_KEY', 'gemini-key')
    @patch('ai_client._try_provider')
    def test_gemini_fallback_when_others_fail(self, mock_try):
        """ModelScope + 火山引擎失败 → Gemini 降级成功"""
        from ai_client import call_ai

        # ModelScope fails → returns None
        # Volc fails → returns None
        mock_try.return_value = (None, Exception("all failed"))

        with patch('ai_client._call_gemini_model') as mock_gemini:
            mock_gemini.return_value = "gemini result"

            result = call_ai('test prompt')
            assert result == "gemini result"
            assert mock_gemini.call_count == 1
            # Was called with a Gemini model
            assert mock_gemini.call_args[0][0] in ['gemini-3.5-flash', 'gemini-2.5-flash']

    @patch('ai_client.AI_AVAILABLE', True)
    @patch('ai_client.MODELSCOPE_API_KEY', 'ms-key')
    @patch('ai_client.VOLC_API_KEY', 'volc-key')
    @patch('ai_client.GEMINI_API_KEY', 'gemini-key')
    @patch('ai_client._try_provider')
    def test_all_providers_fail_raises(self, mock_try):
        """全部提供商失败 → raise last_error"""
        from ai_client import call_ai

        mock_try.return_value = (None, Exception("all providers down"))

        with patch('ai_client._call_gemini_model') as mock_gemini:
            mock_gemini.side_effect = [Exception("gemini also failed")]

            with pytest.raises(Exception):
                call_ai('test prompt')
            # Gemini was tried (at least once) before raising
            assert mock_gemini.call_count >= 1


class TestCallAiNoApiKey:
    """AI_AVAILABLE 检测"""

    @patch('ai_client.AI_AVAILABLE', True)
    @patch('ai_client.MODELSCOPE_API_KEY', None)
    @patch('ai_client.SENSENOVA_API_KEY', None)
    @patch('ai_client.VOLC_API_KEY', None)
    @patch('ai_client.GEMINI_API_KEY', None)
    def test_no_providers_at_all(self):
        """所有 API Key 为 None 时 AI_AVAILABLE=True → 因为没有提供商仍然执行"""
        from ai_client import call_ai
        # With no API keys set but AI_AVAILABLE=True (patched),
        # the function skips all provider blocks and raises
        with pytest.raises(Exception, match="所有 AI 模型均不可用"):
            call_ai('test')


class TestCallAiGeminiRaise:
    """call_ai Gemini 全失败后 raise（line 185 区域）"""

    @patch('ai_client.AI_AVAILABLE', True)
    @patch('ai_client.MODELSCOPE_API_KEY', 'ms-key')
    @patch('ai_client.VOLC_API_KEY', 'volc-key')
    @patch('ai_client.GEMINI_API_KEY', 'gemini-key')
    @patch('ai_client._try_provider')
    def test_gemini_all_fail_after_volc(self, mock_try):
        """Volc 和 Gemini 均失败，最终 raise"""
        from ai_client import call_ai
        mock_try.return_value = (None, Exception("volc failed"))

        with patch('ai_client._call_gemini_model') as mock_gemini:
            mock_gemini.side_effect = [Exception("gemini1 fail"), Exception("gemini2 fail")]

            with pytest.raises(Exception):
                call_ai('test prompt')
            # Gemini 应该被尝试了两次（2 个模型）
            assert mock_gemini.call_count == 2


class TestStreamChatSenseNovaFail:
    """call_chat_stream SenseNova 失败后无 ModelScope 时 raise（line 244）"""

    @patch('ai_client.SENSENOVA_API_KEY', 'fake-key')
    @patch('ai_client.MODELSCOPE_API_KEY', None)
    @patch('openai.OpenAI')
    def test_sensenova_fail_no_fallback(self, mock_openai_cls):
        from ai_client import call_chat_stream
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("SenseNova down")

        with pytest.raises(Exception):
            list(call_chat_stream([{"role": "user", "content": "hi"}]))


class TestStreamChatNoApiKey:
    """call_chat_stream 无任何 API Key 时 raise（line 256）"""

    @patch('ai_client.SENSENOVA_API_KEY', None)
    @patch('ai_client.MODELSCOPE_API_KEY', None)
    def test_no_api_key_raises(self):
        from ai_client import call_chat_stream
        with pytest.raises(Exception, match="未配置聊天 AI 服务"):
            list(call_chat_stream([{"role": "user", "content": "hi"}]))


class TestStreamChatDirect:
    """_stream_chat 直接测试"""

    def test_extra_body_provided(self):
        """直接调用 _stream_chat 传 extra_body"""
        from ai_client import _stream_chat

        mock_client = MagicMock()
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = "test"
        mock_client.chat.completions.create.return_value = [chunk]

        result = list(_stream_chat(mock_client, 'test-model',
                                    [{"role": "user", "content": "hi"}],
                                    extra_body={"extra": "value"}))
        assert result == ["test"]
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs.get('extra_body') == {"extra": "value"}
