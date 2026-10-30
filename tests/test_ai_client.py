"""ai_client.py 测试 — mock OpenAI 测试统一降级链、_try_provider、call_ai、流式聊天"""
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest


@contextmanager
def ai_keys(**which):
    """把四家 API Key 全部置空,只保留 which 里指定的那家。

    本机 .env 有真实 Key,不显式置空会让测试真的去调外网。
    """
    with patch.multiple(
        'ai_client',
        INTERN_API_KEY=which.get('intern'),
        SENSENOVA_API_KEY=which.get('sensenova'),
        AGNES_API_KEY=which.get('agnes'),
        MODELSCOPE_API_KEY=which.get('modelscope'),
    ):
        yield


def _chunk(text):
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = text
    return chunk


class TestAiClientConfig:
    """模块级配置常量测试"""

    def test_ai_available_detection(self):
        from ai_client import AGNES_API_KEY, AI_AVAILABLE, INTERN_API_KEY, MODELSCOPE_API_KEY, SENSENOVA_API_KEY
        expected = any([INTERN_API_KEY, SENSENOVA_API_KEY, AGNES_API_KEY, MODELSCOPE_API_KEY])
        assert expected == AI_AVAILABLE

    def test_chat_available(self):
        from ai_client import AGNES_API_KEY, CHAT_AVAILABLE, INTERN_API_KEY, MODELSCOPE_API_KEY, SENSENOVA_API_KEY
        expected = any([INTERN_API_KEY, SENSENOVA_API_KEY, AGNES_API_KEY, MODELSCOPE_API_KEY])
        assert expected == CHAT_AVAILABLE

    def test_chat_models_come_from_registry(self):
        import settings
        for provider_id in settings.AI_PROVIDER_ORDER:
            assert settings.AI_PROVIDERS[provider_id]['models']['chat']

    def test_chat_shares_the_same_order(self):
        """健康助手不再走另一套顺序,与 call_ai 共用 AI_PROVIDER_ORDER。"""
        import settings
        assert settings.AI_PROVIDERS['intern']['models']['chat'][0] == 'Sage-A1.5'
        assert settings.AI_PROVIDERS['sensenova']['models']['chat'][0] == 'deepseek-flash'


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

    @patch('ai_client._call_openai_compatible')
    def test_extra_body_also_applied_to_vision(self, mock_call):
        """视觉调用同样要关思考,否则思考型模型 content 是空的。"""
        from ai_client import _try_provider
        mock_call.return_value = "success"
        models_config = {
            'vision': ['v1'],
            'extra_body': {'chat_template_kwargs': {'enable_thinking': False}},
        }

        _try_provider(
            'fake-key', 'https://api.test.com', models_config,
            has_images=True, prompt='look', images_data=[b'fake'],
            mime_type='image/jpeg', provider='Test', task_type='vision'
        )
        assert mock_call.call_args[1].get('extra_body') == {
            'chat_template_kwargs': {'enable_thinking': False}
        }

    @patch('ai_client._call_openai_compatible')
    def test_empty_content_treated_as_failure(self, mock_call):
        """思考型模型只填 reasoning_content 时 content 是空串,必须继续降级而不是当成功。"""
        from ai_client import _try_provider
        mock_call.side_effect = ["", "   ", "real answer"]
        models_config = {'text': ['m1', 'm2', 'm3'], 'vision': []}

        result, err = _try_provider(
            'fake-key', 'https://api.test.com', models_config,
            has_images=False, prompt='hello', images_data=None,
            mime_type=None, provider='Test'
        )
        assert result == "real answer"
        assert err is None
        assert mock_call.call_count == 3

    @patch('ai_client._call_openai_compatible')
    def test_timeout_forwarded(self, mock_call):
        from ai_client import _try_provider
        mock_call.return_value = "success"
        _try_provider(
            'fake-key', 'https://api.test.com', {'text': ['m1'], 'vision': []},
            has_images=False, prompt='hello', images_data=None,
            mime_type=None, provider='Test', timeout=42
        )
        assert mock_call.call_args[1].get('timeout') == 42

    @patch('ai_client.time.sleep')
    @patch('ai_client._call_openai_compatible')
    def test_retries_once_after_rate_limit(self, mock_call, mock_sleep):
        """429/超时是瞬时错误,先退避重试一次再考虑换家。"""
        from ai_client import _try_provider
        mock_call.side_effect = [Exception("Error code: 429 - rate limit"), "ok"]

        result, err = _try_provider(
            'fake-key', 'https://api.test.com', {'text': ['m1'], 'vision': []},
            has_images=False, prompt='hello', images_data=None,
            mime_type=None, provider='Test'
        )
        assert result == "ok"
        assert err is None
        assert mock_call.call_count == 2
        mock_sleep.assert_called_once()

    @patch('ai_client.time.sleep')
    @patch('ai_client._call_openai_compatible')
    def test_does_not_retry_deterministic_failure(self, mock_call, mock_sleep):
        """普通错误不重试,直接换下一个模型。"""
        from ai_client import _try_provider
        mock_call.side_effect = [Exception("boom"), "ok"]

        result, _ = _try_provider(
            'fake-key', 'https://api.test.com', {'text': ['m1', 'm2'], 'vision': []},
            has_images=False, prompt='hello', images_data=None,
            mime_type=None, provider='Test'
        )
        assert result == "ok"
        assert mock_call.call_count == 2
        mock_sleep.assert_not_called()

    @patch('ai_client.time.sleep')
    @patch('ai_client._call_openai_compatible')
    def test_empty_content_is_not_retried(self, mock_call, mock_sleep):
        """空内容是确定性失败,重试也没用,直接换家。"""
        from ai_client import _try_provider
        mock_call.side_effect = ["", "ok"]

        result, _ = _try_provider(
            'fake-key', 'https://api.test.com', {'text': ['m1', 'm2'], 'vision': []},
            has_images=False, prompt='hello', images_data=None,
            mime_type=None, provider='Test'
        )
        assert result == "ok"
        assert mock_call.call_count == 2
        mock_sleep.assert_not_called()

    def test_is_retryable(self):
        from ai_client import _is_retryable

        class APITimeoutError(Exception):
            pass

        class RateLimitError(Exception):
            pass

        assert _is_retryable(APITimeoutError("")) is True
        assert _is_retryable(RateLimitError("")) is True
        assert _is_retryable(Exception("Error code: 429")) is True
        assert _is_retryable(Exception("Request timed out.")) is True
        assert _is_retryable(Exception("model not found")) is False
        assert _is_retryable(ValueError("返回空内容")) is False


class TestCallAi:
    """call_ai() 统一降级链测试"""

    @patch('ai_client.AI_AVAILABLE', False)
    def test_raises_when_no_ai_available(self):
        from ai_client import call_ai
        with pytest.raises(Exception, match='AI 服务未配置'):
            call_ai('test prompt')

    @patch('ai_client.AI_AVAILABLE', True)
    @patch('ai_client._try_provider')
    def test_first_provider_in_order_wins(self, mock_try):
        from ai_client import call_ai
        mock_try.return_value = ("intern result", None)
        with ai_keys(intern='k1', sensenova='k2'):
            assert call_ai('test prompt') == "intern result"
        assert mock_try.call_args[1]['provider'] == '端砚'

    @patch('ai_client.AI_AVAILABLE', True)
    @patch('ai_client._try_provider')
    def test_falls_back_to_next_provider(self, mock_try):
        from ai_client import call_ai
        seen = []

        def side_effect(*args, **kwargs):
            seen.append(kwargs['provider'])
            if kwargs['provider'] == '端砚':
                return (None, Exception("intern down"))
            return ("sensenova result", None)

        mock_try.side_effect = side_effect
        with ai_keys(intern='k1', sensenova='k2'):
            assert call_ai('test prompt') == "sensenova result"
        assert seen == ['端砚', 'SenseNova']

    @patch('ai_client.AI_AVAILABLE', True)
    @patch('ai_client._try_provider')
    def test_skips_provider_without_key(self, mock_try):
        from ai_client import call_ai
        mock_try.return_value = ("ok", None)
        with ai_keys(agnes='k3'):
            assert call_ai('test prompt') == "ok"
        assert mock_try.call_args[1]['provider'] == 'Agnes'

    @patch('ai_client.AI_AVAILABLE', True)
    @patch('ai_client._try_provider')
    def test_skips_provider_without_that_task_models(self, mock_try):
        """某家没配该任务的模型就跳过,不能 KeyError 也不能空调用。"""
        import settings
        from ai_client import call_ai
        mock_try.return_value = ("ok", None)
        empty = {**settings.AI_PROVIDERS['sensenova'], 'models': {}}
        with patch.dict('settings.AI_PROVIDERS', {'sensenova': empty}):
            with ai_keys(sensenova='k2', modelscope='k4'):
                assert call_ai('test prompt') == "ok"
        assert mock_try.call_args[1]['provider'] == 'ModelScope'

    @patch('ai_client.AI_AVAILABLE', True)
    @patch('ai_client._try_provider')
    def test_has_images_forces_vision(self, mock_try):
        from ai_client import call_ai
        mock_try.return_value = ("result", None)
        with ai_keys(intern='k1'):
            call_ai('test', images_data=[b'fake'])
        assert mock_try.call_args[1]['task_type'] == 'vision'

    @patch('ai_client.AI_AVAILABLE', True)
    @patch('ai_client._try_provider')
    def test_defaults_to_text_task(self, mock_try):
        from ai_client import call_ai
        mock_try.return_value = ("result", None)
        with ai_keys(intern='k1'):
            call_ai('test')
        assert mock_try.call_args[1]['task_type'] == 'text'

    @patch('ai_client.AI_AVAILABLE', True)
    @patch('ai_client._try_provider')
    def test_vision_uses_its_own_provider_order(self, mock_try):
        """vision 单独配成 ModelScope 优先,text 仍走默认顺序。"""
        from ai_client import call_ai
        mock_try.return_value = ("ok", None)

        with ai_keys(intern='k1', modelscope='k4'):
            call_ai('看图', images_data=[b'fake'])
        assert mock_try.call_args[1]['provider'] == 'ModelScope'

        mock_try.reset_mock()
        with ai_keys(intern='k1', modelscope='k4'):
            call_ai('纯文本')
        assert mock_try.call_args[1]['provider'] == '端砚'

    @patch('ai_client.AI_AVAILABLE', True)
    @patch('ai_client._try_provider')
    def test_all_providers_fail(self, mock_try):
        from ai_client import call_ai
        mock_try.return_value = (None, Exception("all failed"))
        with ai_keys(intern='k1', sensenova='k2', agnes='k3', modelscope='k4'):
            with pytest.raises(Exception, match="all failed"):
                call_ai('test prompt')

    @patch('ai_client.AI_AVAILABLE', True)
    def test_no_providers_at_all(self):
        from ai_client import call_ai
        with ai_keys():
            with pytest.raises(Exception, match="所有 AI 模型均不可用"):
                call_ai('test')


class TestCallChatStream:
    """call_chat_stream() 流式聊天测试 — 与 call_ai 同一条供应商顺序"""

    @patch('openai.OpenAI')
    def test_first_provider_streams(self, mock_openai_cls):
        from ai_client import call_chat_stream
        client = MagicMock()
        mock_openai_cls.return_value = client
        client.chat.completions.create.return_value = [
            _chunk("Hello"), _chunk(" World"), _chunk(None)
        ]
        with ai_keys(intern='k1', sensenova='k2'):
            assert list(call_chat_stream([{"role": "user", "content": "hi"}])) == ["Hello", " World"]
        assert client.chat.completions.create.call_args[1]['model'] == 'Sage-A1.5'

    @patch('openai.OpenAI')
    def test_falls_back_when_first_provider_has_no_key(self, mock_openai_cls):
        from ai_client import call_chat_stream
        client = MagicMock()
        mock_openai_cls.return_value = client
        client.chat.completions.create.return_value = [_chunk("fallback")]
        with ai_keys(sensenova='k2'):
            assert list(call_chat_stream([{"role": "user", "content": "hi"}])) == ["fallback"]
        assert client.chat.completions.create.call_args[1]['model'] == 'deepseek-flash'

    @patch('openai.OpenAI')
    def test_falls_back_when_create_raises_before_yield(self, mock_openai_cls):
        from ai_client import call_chat_stream
        good = MagicMock()
        good.chat.completions.create.return_value = [_chunk("second")]

        def factory(**kwargs):
            if kwargs['base_url'] == 'https://discovery-api.intern-ai.org.cn/v1':
                raise Exception("intern down")
            return good

        mock_openai_cls.side_effect = factory
        with ai_keys(intern='k1', sensenova='k2'):
            assert list(call_chat_stream([{"role": "user", "content": "hi"}])) == ["second"]

    @patch('openai.OpenAI')
    def test_empty_stream_falls_through(self, mock_openai_cls):
        """思考型模型可能只流 reasoning_content、content 一个字都没有。"""
        from ai_client import call_chat_stream
        empty = MagicMock()
        empty.chat.completions.create.return_value = [_chunk(None)]
        good = MagicMock()
        good.chat.completions.create.return_value = [_chunk("real")]

        def factory(**kwargs):
            if kwargs['base_url'] == 'https://discovery-api.intern-ai.org.cn/v1':
                return empty
            return good

        mock_openai_cls.side_effect = factory
        with ai_keys(intern='k1', sensenova='k2'):
            assert list(call_chat_stream([{"role": "user", "content": "hi"}])) == ["real"]

    @patch('openai.OpenAI')
    def test_mid_stream_failure_raises_without_repeating(self, mock_openai_cls):
        """已经开始吐字后再失败,不能再换一家重头来,否则用户会看到重复内容。"""
        from ai_client import call_chat_stream
        client = MagicMock()

        def gen():
            yield _chunk("partial")
            raise Exception("network broke")

        client.chat.completions.create.return_value = gen()
        mock_openai_cls.return_value = client

        received = []
        with ai_keys(intern='k1', sensenova='k2'):
            with pytest.raises(Exception, match="network broke"):
                for piece in call_chat_stream([{"role": "user", "content": "hi"}]):
                    received.append(piece)
        assert received == ["partial"]
        assert mock_openai_cls.call_count == 1

    @patch('openai.OpenAI')
    def test_all_chat_providers_fail(self, mock_openai_cls):
        from ai_client import call_chat_stream
        mock_openai_cls.side_effect = Exception("all down")
        with ai_keys(intern='k1', sensenova='k2', agnes='k3', modelscope='k4'):
            with pytest.raises(Exception):  # noqa: B017
                list(call_chat_stream([{"role": "user", "content": "hi"}]))

    def test_no_api_key_raises(self):
        from ai_client import call_chat_stream
        with ai_keys():
            with pytest.raises(Exception, match="未配置聊天 AI 服务"):
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
    def test_timeout_and_retry_forwarded(self, mock_httpx_client, mock_openai_cls):
        from ai_client import _call_openai_compatible
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="ok"))]
        )

        _call_openai_compatible('key', 'https://api.test.com/v1', 'm', 'hi',
                                provider='Test', timeout=42)
        assert mock_openai_cls.call_args[1]['timeout'] == 42
        # SDK 默认会自己重试 2 次,叠上四家供应商链会等到天荒地老,统一压到 1 次
        assert mock_openai_cls.call_args[1]['max_retries'] == 1

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
