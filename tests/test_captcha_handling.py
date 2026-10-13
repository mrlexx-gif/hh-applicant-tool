"""Тесты обработки капчи в потоке откликов (apply).

Проверяем два места, где hh.ru требует капчу:

* API отвечает 403 с ``captcha_required`` — ``BaseClient`` должен позвать
  ``captcha_handler`` и повторить запрос;
* JSON-ответ XHR содержит ``hhcaptcha.captchaState`` — ``Operation``
  должен распознать состояние и прогнать картинку через ``CaptchaFlow``.

Тесты оффлайн: поддельные сессии/флоу, сеть не нужна.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from hh_applicant_tool.api.client import ApiClient
from hh_applicant_tool.api.errors import CaptchaRequired
from hh_applicant_tool.operations import apply_vacancies as av
from hh_applicant_tool.operations.apply_vacancies import Operation

CAPTCHA_URL = "https://hh.ru/account/captcha?state=abc123"
CAPTCHA_403 = {
    "errors": [{"value": "captcha_required", "captcha_url": CAPTCHA_URL}]
}


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        data: dict | None = None,
        text: str = "{}",
    ) -> None:
        self.status_code = status_code
        self._data = data if data is not None else {}
        self.text = text
        self.headers: dict[str, str] = {}
        self.url = "https://api.hh.ru/negotiations"
        self.request = SimpleNamespace(method="POST")

    def json(self) -> dict:
        return self._data


class FakeSession(requests.Session):
    def __init__(self, responses: list[FakeResponse]) -> None:
        super().__init__()
        self._responses = list(responses)
        self.calls = 0

    def request(self, *args, **kwargs) -> FakeResponse:
        self.calls += 1
        return self._responses.pop(0)


def make_client(responses: list[FakeResponse]) -> ApiClient:
    return ApiClient(
        access_token=None,
        delay=0.001,
        captcha_cooldown=0.001,
        session=FakeSession(responses),
    )


class TestApiCaptchaRetry:
    """403 captcha_required → handler → повтор запроса."""

    def test_retries_after_handler_solves(self) -> None:
        client = make_client(
            [
                FakeResponse(403, CAPTCHA_403),
                FakeResponse(200, {}, text="{}"),
            ]
        )
        handler = MagicMock(return_value=True)
        client.captcha_handler = handler

        assert client.post("/negotiations", {}) == {}
        handler.assert_called_once_with(CAPTCHA_URL)
        assert client.session.calls == 2

    def test_raises_when_handler_not_set(self) -> None:
        client = make_client([FakeResponse(403, CAPTCHA_403)])
        with pytest.raises(CaptchaRequired):
            client.post("/negotiations", {})
        assert client.session.calls == 1

    def test_raises_when_handler_fails(self) -> None:
        client = make_client([FakeResponse(403, CAPTCHA_403)])
        client.captcha_handler = MagicMock(return_value=False)
        with pytest.raises(CaptchaRequired):
            client.post("/negotiations", {})
        assert client.session.calls == 1


class TestCaptchaStateFrom:
    def test_hhcaptcha_state(self) -> None:
        data = {"hhcaptcha": {"isBot": True, "captchaState": "S1"}}
        assert Operation._captcha_state_from(data) == "S1"

    def test_recaptcha_state(self) -> None:
        data = {"recaptcha": {"captchaState": "S2"}}
        assert Operation._captcha_state_from(data) == "S2"

    def test_absent(self) -> None:
        assert Operation._captcha_state_from({"ok": True}) is None
        assert Operation._captcha_state_from("nope") is None


def _operation() -> Operation:
    operation = Operation()
    operation.tool = SimpleNamespace(session=object(), base_url="https://hh.ru")
    operation._args = SimpleNamespace(captcha_language="en")
    return operation


class TestSolveCaptchaChallenge:
    def _patch_flow(self, monkeypatch) -> dict:
        created: dict = {}

        class FakeFlow:
            def __init__(self, session, url, *, language=None, backurl=None):
                created["url"] = url
                created["language"] = language

            def prime(self):
                created["primed"] = True

            def fetch(self):
                return SimpleNamespace(key="k", image=b"png")

            def submit(self, text, *, key):
                created["text"] = text
                created["key"] = key
                return SimpleNamespace(accepted=True, reason="accepted")

        monkeypatch.setattr(av, "CaptchaFlow", FakeFlow)
        return created

    def test_inline_state_without_prime(self, monkeypatch) -> None:
        created = self._patch_flow(monkeypatch)
        operation = _operation()
        operation._recognize_captcha = lambda _data: "word word"

        assert operation._solve_captcha_challenge(captcha_state="abc") is True

        assert created["url"] == "https://hh.ru/account/captcha?state=abc"
        assert "primed" not in created
        assert created["text"] == "word word"
        assert created["key"] == "k"

    def test_standalone_url_primes(self, monkeypatch) -> None:
        created = self._patch_flow(monkeypatch)
        operation = _operation()
        operation._recognize_captcha = lambda _data: "word word"

        assert (
            operation._solve_captcha_challenge(captcha_url=CAPTCHA_URL) is True
        )

        assert created["url"] == CAPTCHA_URL
        assert created.get("primed") is True

    def test_gives_up_when_not_recognized(self, monkeypatch) -> None:
        self._patch_flow(monkeypatch)
        operation = _operation()
        operation._recognize_captcha = lambda _data: None

        assert operation._solve_captcha_challenge(captcha_state="abc") is False


def _inline_operation() -> Operation:
    operation = Operation()
    operation.tool = MagicMock()
    operation.tool.get_captcha_image.return_value = SimpleNamespace(
        data=b"png", key="k1"
    )
    operation._args = SimpleNamespace(captcha_language="en")
    return operation


class TestSolveInlineCaptcha:
    """Капчу из XHR возвращаем как (text, key) для полей запроса."""

    def test_returns_text_and_key(self) -> None:
        operation = _inline_operation()
        operation._recognize_captcha = lambda _data: "word word"

        assert operation._solve_inline_captcha(
            referer_url="https://hh.ru/applicant/vacancy_response"
        ) == ("word word", "k1")

        call = operation.tool.get_captcha_image.call_args
        assert call.kwargs["referer_url"] == (
            "https://hh.ru/applicant/vacancy_response"
        )

    def test_none_when_not_recognized(self) -> None:
        operation = _inline_operation()
        operation._recognize_captcha = lambda _data: None

        assert operation._solve_inline_captcha() is None

    def test_none_when_image_fetch_fails(self) -> None:
        operation = _inline_operation()
        operation.tool.get_captcha_image.side_effect = RuntimeError("boom")

        assert operation._solve_inline_captcha() is None
