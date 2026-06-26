"""ai_client.py 测试 — mock OpenAI 测试降级链、_try_provider、call_ai"""
from unittest.mock import MagicMock, patch

import pytest


class TestAiClientConfig:
    """模块级配置常量测试"""

    def test_ai_available_detection(self):
        from ai_client import AI_AVAILABLE, MODELSCOPE_API_KEY, SENSENOVA_API_KEY
        expected = bool(MODELSCOPE_API_KEY or SENSENOVA_API_KEY)
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
        assert mock_call.call_args[1].get('extra_body') == {'enable_thinking': False}


class TestCallAi:
    """call_ai() 降级链测试"""

    @patch('ai_client.AI_AVAILABLE', False)
    def test_raises_when_no_ai_available(self):
        from ai_client import call_ai
        with pytest.raises(Exception, match='AI 服务未配置'):
            call_ai('test prompt')

    @patch('ai_client.AI_AVAILABLE', True)
    @patch('ai_client.MODELSCOPE_API_KEY', 'fake-key')
    @patch('ai_client._try_provider')
    def test_modelscope_success(self, mock_try):
        from ai_client import call_ai
        mock_try.return_value = ("modelscope result", None)
        result = call_ai('test prompt')
        assert result == "modelscope result"

    @patch('ai_client.MODELSCOPE_API_KEY', 'fake-key')
    @patch('ai_client._try_provider')
    def test_modelscope_fail_no_fallback(self, mock_try):
        """没有 SenseNova 时 ModelScope 失败直接 raise"""
        from ai_client import call_ai
        mock_try.return_value = (None, Exception("fail"))
        with pytest.raises(Exception):  # noqa: B017
            call_ai('test prompt')
    @patch('ai_client.MODELSCOPE_API_KEY', 'fake-key')
    @patch('ai_client._try_provider')
    def test_has_images_forces_vision(self, mock_try):
        """带图片强制 vision task_type，不走 SenseNova"""
        from ai_client import call_ai
        mock_try.return_value = ("result", None)
        call_ai('test', images_data=[b'fake'])
        assert mock_try.call_args[1].get('task_type') == 'vision'

    @patch('ai_client.AI_AVAILABLE', True)
    @patch('ai_client.MODELSCOPE_API_KEY', 'ms-key')
    @patch('ai_client.SENSENOVA_API_KEY', 'sn-key')
    @patch('ai_client._try_provider')
    def test_modelscope_fail_sensenova_success(self, mock_try):
        """ModelScope 失败 → SenseNova 降级"""
        from ai_client import call_ai
        def side_effect(*args, **kwargs):
            provider = kwargs.get('provider', '')
            if provider == 'ModelScope':
                return (None, Exception("modelscope down"))
            return ("sensenova result", None)
        mock_try.side_effect = side_effect
        result = call_ai('test prompt')
        assert result == "sensenova result"

    @patch('ai_client.AI_AVAILABLE', True)
    @patch('ai_client.MODELSCOPE_API_KEY', 'ms-key')
    @patch('ai_client.SENSENOVA_API_KEY', 'sn-key')
    @patch('ai_client._try_provider')
    def test_both_providers_fail(self, mock_try):
        """ModelScope + SenseNova 都失败 → raise last_error"""
        from ai_client import call_ai
        mock_try.return_value = (None, Exception("all failed"))
        with pytest.raises(Exception, match="all failed"):
            call_ai('test prompt')


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
        assert mock_client.chat.completions.create.call_args[1]['model'] == 'deepseek-v4-flash'

    @patch('ai_client.SENSENOVA_API_KEY', None)
    @patch('ai_client.MODELSCOPE_API_KEY', 'fake-key')
    @patch('openai.OpenAI')
    def test_modelscope_fallback_when_sensenova_missing(self, mock_openai_cls):
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
        from ai_client import call_chat_stream
        mock_openai_cls.side_effect = Exception("all down")

        with pytest.raises(Exception):  # noqa: B017
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
        assert len(call_messages[0]['content']) == 2

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
            'key', 'https://api-inference.modelscope.cn/v1', 'model',
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
        assert mock_openai_cls.call_args[1].get('http_client') is None
        mock_httpx_client.assert_not_called()

    @patch('openai.OpenAI')
    @patch('ai_client.httpx.Client')
    def test_openai_compatible_with_extra_body(self, mock_httpx_client, mock_openai_cls):
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
        create_kwargs = mock_client.chat.completions.create.call_args[1]
        assert create_kwargs.get('extra_body') == {'enable_thinking': False}


class TestCnEndpoint:
    """_call_openai_compatible CN 端点代理绕过"""

    @patch('openai.OpenAI')
    @patch('ai_client.httpx.Client')
    def test_cn_dot_cn_domain(self, mock_httpx, mock_openai):
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
    def test_cn_domain_no_proxy(self, mock_httpx, mock_openai):
        from ai_client import _call_openai_compatible
        mock_httpx.return_value = MagicMock()
        mock_openai.return_value.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="ok"))]
        )

        _call_openai_compatible(
            'key', 'https://api-inference.modelscope.cn/v1', 'm1',
            'hello', provider='Test'
        )
        mock_httpx.assert_called_once_with(trust_env=False)

    @patch('openai.OpenAI')
    @patch('ai_client.httpx.Client')
    def test_non_cn_domain_no_proxy_bypass(self, mock_httpx, mock_openai):
        from ai_client import _call_openai_compatible
        mock_openai.return_value.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="ok"))]
        )

        _call_openai_compatible(
            'key', 'https://api.openai.com/v1', 'm1',
            'hello', provider='Test'
        )
        mock_httpx.assert_not_called()
        assert mock_openai.call_args[1].get('http_client') is None


class TestCallAiNoApiKey:
    """AI_AVAILABLE 检测 — 无任何 API Key"""

    @patch('ai_client.AI_AVAILABLE', True)
    @patch('ai_client.MODELSCOPE_API_KEY', None)
    @patch('ai_client.SENSENOVA_API_KEY', None)
    def test_no_providers_at_all(self):
        """所有 API Key 为 None → 跳过所有提供商 → raise"""
        from ai_client import call_ai
        with pytest.raises(Exception, match="所有 AI 模型均不可用"):
            call_ai('test')


class TestStreamChatSenseNovaFail:
    """call_chat_stream SenseNova 失败后无 ModelScope 时 raise"""

    @patch('ai_client.SENSENOVA_API_KEY', 'fake-key')
    @patch('ai_client.MODELSCOPE_API_KEY', None)
    @patch('openai.OpenAI')
    def test_sensenova_fail_no_fallback(self, mock_openai_cls):
        from ai_client import call_chat_stream
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("SenseNova down")

        with pytest.raises(Exception):  # noqa: B017
            list(call_chat_stream([{"role": "user", "content": "hi"}]))


class TestStreamChatNoApiKey:
    """call_chat_stream 无任何 API Key 时 raise"""

    @patch('ai_client.SENSENOVA_API_KEY', None)
    @patch('ai_client.MODELSCOPE_API_KEY', None)
    def test_no_api_key_raises(self):
        from ai_client import call_chat_stream
        with pytest.raises(Exception, match="未配置聊天 AI 服务"):
            list(call_chat_stream([{"role": "user", "content": "hi"}]))


class TestStreamChatDirect:
    """_stream_chat 直接测试"""

    def test_extra_body_provided(self):
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
