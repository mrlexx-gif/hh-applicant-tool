"""Регрессионные тесты разбора конфига OpenAI.

Проверяют два дефекта, из-за которых отклики с AI падали с
``ValueError: Значение 'base_url' не задано ...`` и затем с
``TypeError: ChatOpenAI.__init__() missing 1 required positional
argument: 'api_key'``:

1. Конфиг может быть задан «плоскими» секциями через подчёркивание
   (``[openai_cover_letter]``), как их пишет UI, а не только вложенными
   (``[openai.cover_letter]``). Рантайм обязан читать оба варианта.
2. ``get_openai_args`` обязан пробрасывать ``api_key`` в клиент.
"""

from __future__ import annotations

import pytest

from hh_applicant_tool.tool import HHApplicantTool

LEGACY_COVER_LETTER = {
    "api_key": "sk-legacy",
    "base_url": "https://api.deepseek.com/chat/completions",
    "model": "deepseek-chat",
    "max_completion_tokens": 1000,
    "temperature": 0.3,
}


class _StubTool:
    """Минимальный носитель метода get_openai_config из HHApplicantTool."""

    def __init__(self, config: dict) -> None:
        self.config = config
        self.openai_delay = None
        self.openai_timeout = None
        self.openai_connect_timeout = None

    get_openai_config = HHApplicantTool.get_openai_config
    get_openai_args = HHApplicantTool.get_openai_args
    get_ai_client = HHApplicantTool.get_ai_client


def test_legacy_flat_section_is_read():
    """Старая «плоская» секция openai_cover_letter должна читаться."""
    tool = _StubTool({"openai_cover_letter": dict(LEGACY_COVER_LETTER)})

    args = tool.get_openai_args("openai.cover_letter")

    assert args["api_key"] == "sk-legacy"
    assert args["base_url"] == "https://api.deepseek.com/chat/completions"
    assert args["model"] == "deepseek-chat"


def test_nested_section_takes_precedence_over_legacy():
    """Вложенная секция openai.cover_letter перекрывает плоскую."""
    tool = _StubTool(
        {
            "openai_cover_letter": dict(LEGACY_COVER_LETTER),
            "openai": {
                "cover_letter": {"model": "nested-model"},
            },
        }
    )

    args = tool.get_openai_args("openai.cover_letter")

    assert args["model"] == "nested-model"
    # Остальные значения наследуются из плоской секции.
    assert args["api_key"] == "sk-legacy"


def test_api_key_is_forwarded_to_ai_client():
    """get_openai_args должен прокидывать api_key в ChatOpenAI."""
    tool = _StubTool({"openai_cover_letter": dict(LEGACY_COVER_LETTER)})

    args = tool.get_openai_args("openai.cover_letter")
    args.setdefault("system_prompt", "test")

    client = tool.get_ai_client(**args)

    assert client.api_key == "sk-legacy"
    assert client.base_url == "https://api.deepseek.com/chat/completions"


def test_missing_config_raises_value_error():
    """Без base_url/api_key/model должно падать понятной ошибкой."""
    tool = _StubTool({})

    with pytest.raises(ValueError, match="base_url"):
        tool.get_openai_config("openai.cover_letter")
