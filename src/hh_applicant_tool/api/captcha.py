"""Разбор ответа hh.ru на отправленный ответ капчи.

Раньше успех определялся по DOM страницы — ждали, пока пропадёт поле
ввода, а по таймауту объявляли капчу решённой. Зависание hh.ru при этом
считалось успехом, и инструмент уходил откликаться с капчей, которую
никто не проходил.

Теперь успех определяется по самому ответу hh.ru на POST. Правила
разбора сняты с чужого реверса и подтверждены в JS самого hh.ru:

* JSON с ``hhcaptcha.isBot`` или ``recaptcha.isBot`` true — отказ;
* 302/303 на ``/account/captcha`` или ``/account/login`` — отказ;
* любой другой 302/303, включая совсем пустой ``Location``, — принято:
  фронтенд hh.ru трактует 302 у XHR как завершение и сам уходит на
  backurl;
* 200 без маркера ``isBot`` — принято;
* всё остальное — «непонятно», успехом считать нельзя.

Класс :class:`CaptchaFlow` работает поверх обычной ``requests.Session``
(или любой совместимой), поэтому его легко подменить в тестах.
"""

from __future__ import annotations

import json
import logging
from typing import Any, NamedTuple
from urllib.parse import parse_qs, urlsplit

__all__ = (
    "CAPTCHA_LANGUAGE_DEFAULT",
    "REASON_ACCEPTED",
    "CaptchaError",
    "CaptchaFlow",
    "CaptchaImage",
    "CaptchaResult",
)

logger = logging.getLogger("hh-applicant-tool")

# Язык картинки капчи по умолчанию. hh.ru понимает и верхний, и нижний
# регистр, наружу отдаёт значение в верхнем.
CAPTCHA_LANGUAGE_DEFAULT = "en"

# Причина, по которой ответ капчи принят.
REASON_ACCEPTED = "accepted"

# Маркер «бота» может прийти в любой из этих секций ответа.
_BOT_SECTIONS = ("hhcaptcha", "recaptcha")

# Страницы, редирект на которые означает, что ответ не принят. Путь в
# ответе сравнивается без ведущего слэша.
_REJECT_PATHS = ("account/captcha", "account/login")

# Коды, у которых hh.ru отдаёт результат капчи как редирект.
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)

# Форсируем английский язык на всех запросах, связанных с капчей: иначе
# hh.ru (у которого язык сессии русский) может вернуть кириллическую
# картинку, а промпт у нас латинский — и ответ всегда будет неверным.
_ACCEPT_LANGUAGE_EN = "en-US,en;q=0.9"


class CaptchaError(Exception):
    """Капчу не удалось ни получить, ни отправить."""


class CaptchaImage(NamedTuple):
    key: str
    image: bytes


class CaptchaResult(NamedTuple):
    accepted: bool
    reason: str


def _session_cookie(session: Any, name: str) -> str | None:
    """Достаёт cookie по имени из jar любой реализации CookieJar."""
    cookies = getattr(session, "cookies", None)
    if cookies is None:
        return None

    getter = getattr(cookies, "get", None)
    if callable(getter):
        try:
            value = getter(name)
        except TypeError:
            value = None
        if value:
            return value

    for cookie in cookies:
        if getattr(cookie, "name", None) == name:
            return getattr(cookie, "value", None)

    return None


class CaptchaFlow:
    """Одна сессия прохождения капчи hh.ru.

    Шаги независимы и могут вызываться по нескольку раз:

    * :meth:`prime` — открыть страницу капчи (запоминает боевой домен,
      на региональные шарды hh.ru);
    * :meth:`fetch` — получить свежую картинку и её ``key``;
    * :meth:`submit` — отправить распознанный текст и разобрать ответ.
    """

    def __init__(
        self,
        session: Any,
        captcha_url: str,
        *,
        language: str | None = None,
        backurl: str | None = None,
    ) -> None:
        self.session = session
        self.captcha_url = captcha_url

        split = urlsplit(captcha_url)
        query = parse_qs(split.query)

        state = (query.get("state") or [""])[0]
        if not state:
            raise CaptchaError("В ссылке на капчу нет параметра state")

        self.state = state
        self.origin = f"{split.scheme}://{split.netloc}"
        self._language = self._normalize_language(language)

        if backurl is None and (values := query.get("backurl")):
            backurl = values[0]
        self.backurl = backurl or f"{self.origin}/"

    @staticmethod
    def _normalize_language(language: str | None) -> str:
        if not language or not str(language).strip():
            return CAPTCHA_LANGUAGE_DEFAULT.upper()
        return str(language).strip().upper()

    @property
    def language(self) -> str:
        return self._language

    def prime(self):
        """Открывает страницу капчи и запоминает итоговый домен."""
        response = self.session.get(
            self.captcha_url,
            headers={"Accept-Language": _ACCEPT_LANGUAGE_EN},
        )

        split = urlsplit(getattr(response, "url", "") or self.captcha_url)
        if split.scheme and split.netloc:
            self.origin = f"{split.scheme}://{split.netloc}"

        logger.debug("страница капчи открыта: %s", self.origin)
        return response

    def fetch(self) -> CaptchaImage:
        """Запрашивает новую картинку. Каждый вызов — свежая картинка."""
        headers = {
            "Accept": "application/json",
            "Accept-Language": _ACCEPT_LANGUAGE_EN,
            "X-Requested-With": "XMLHttpRequest",
            "X-Hhtmfrom": "",
            "X-Hhtmsource": "account_captcha",
        }
        if xsrf := _session_cookie(self.session, "_xsrf"):
            headers["X-Xsrftoken"] = xsrf

        response = self.session.post(
            f"{self.origin}/captcha",
            params={"lang": self.language},
            headers=headers,
        )

        data = self._json(response)
        key = data.get("key") if isinstance(data, dict) else None
        if not key:
            raise CaptchaError("hh.ru не вернул ключ капчи")

        response = self.session.get(
            f"{self.origin}/captcha/picture",
            params={"key": key},
            headers={
                "Accept": "image/png,image/*",
                "Accept-Language": _ACCEPT_LANGUAGE_EN,
            },
        )

        image = getattr(response, "content", b"") or b""
        if not image:
            raise CaptchaError("Картинка капчи пришла пустой")

        return CaptchaImage(key=key, image=image)

    def submit(self, text: str, *, key: str) -> CaptchaResult:
        """Отправляет ответ и по ответу hh.ru решает, принят ли он."""
        xsrf = _session_cookie(self.session, "_xsrf")
        if not xsrf:
            raise CaptchaError(
                "Не найден cookie _xsrf — капчу нельзя отправить"
            )

        params = {
            "captchaText": text,
            "captchaKey": key,
            "captchaState": self.state,
            "backurl": self.backurl,
            "failurl": self.captcha_url,
        }
        headers = {
            "Accept": "application/json",
            "Accept-Language": _ACCEPT_LANGUAGE_EN,
            "Origin": self.origin,
            "X-Requested-With": "XMLHttpRequest",
            "X-Xsrftoken": xsrf,
            "X-Hhtmfrom": "",
            "X-Hhtmsource": "account_captcha",
            "Referer": self.captcha_url,
        }

        # В тело добавляем _xsrf (как в форме hh.ru); query оставляем для
        # совместимости с тестами.
        body = {**params, "_xsrf": xsrf}

        response = self.session.post(
            f"{self.origin}/account/captcha",
            params=params,
            data=body,
            headers=headers,
            allow_redirects=False,
        )

        result = self._classify(response)
        if result.accepted:
            logger.debug(
                "ответ hh.ru на капчу: принят (%s), status=%s",
                result.reason,
                getattr(response, "status_code", None),
            )
        else:
            # Разбираем тело: captchaError=True — неверный текст,
            # isBot=True без captchaError — отказ уровня анти-бота.
            data = self._json(response)
            sections: dict[str, dict] = {}
            if isinstance(data, dict):
                for name in _BOT_SECTIONS:
                    value = data.get(name)
                    if isinstance(value, dict):
                        sections[name] = {
                            k: value.get(k)
                            for k in ("isBot", "captchaError")
                            if k in value
                        }
            logger.debug(
                "ответ hh.ru на капчу отклонён (%s): status=%s sections=%r",
                result.reason,
                getattr(response, "status_code", None),
                sections,
            )
        return result

    def _classify(self, response: Any) -> CaptchaResult:
        data = self._json(response)
        # Признак бота важнее кода ответа: 303 с isBot всё равно отказ.
        if isinstance(data, dict) and self._is_bot(data):
            return CaptchaResult(accepted=False, reason="isBot")

        status = getattr(response, "status_code", 0)

        if status in _REDIRECT_STATUSES:
            location = self._location(response)
            target = urlsplit(location).path.lstrip("/") if location else ""
            if target.startswith(_REJECT_PATHS):
                return CaptchaResult(
                    accepted=False, reason=f"redirect_{target}"
                )
            # Пустой Location — это тоже принято: фронтенд сам уходит на
            # backurl.
            return CaptchaResult(accepted=True, reason=REASON_ACCEPTED)

        if status == 200:
            return CaptchaResult(accepted=True, reason=REASON_ACCEPTED)

        return CaptchaResult(accepted=False, reason=f"http_{status}")

    @staticmethod
    def _is_bot(data: dict[str, Any]) -> bool:
        for section in _BOT_SECTIONS:
            value = data.get(section)
            if isinstance(value, dict) and value.get("isBot"):
                return True
        return False

    @staticmethod
    def _location(response: Any) -> str:
        headers = getattr(response, "headers", None) or {}
        return headers.get("Location") or headers.get("location") or ""

    @staticmethod
    def _json(response: Any) -> Any:
        content = getattr(response, "content", b"") or b""
        if not content:
            return None
        try:
            return json.loads(content.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
