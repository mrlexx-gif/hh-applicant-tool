# Блять, если класс работает с OpenAI API, то он не должен знать ничего про
# API HH и ваши проблемы с капчей. Прекратите добавлять методы, которые выходят
# за его зону ответственности.
import base64
import json
import logging
import re
import time
from dataclasses import KW_ONLY, dataclass, field
from email.utils import parsedate_to_datetime
from threading import Lock

import requests
from urllib3.util import Timeout

from ..utils.common import get_proxies_dict
from .base import AIError

DEFAULT_DELAY = 0.5
DEFAULT_TIMEOUT = 60.0
DEFAULT_CONNECT_TIMEOUT = 10.0


logger = logging.getLogger("hh-applicant-tool")


class OpenAIError(AIError):
    pass


# Ожидаемый алфавит ответа капчи. Язык картинки однозначно задаёт
# алфавит: кириллица и латиница не взаимозаменяемы.
CAPTCHA_SCRIPT_LATIN = "latin"
CAPTCHA_SCRIPT_CYRILLIC = "cyrillic"
CAPTCHA_SCRIPT_ANY = "any"

# Общая часть промпта: что в картинке и в каком формате вернуть ответ.
# Алфавит сюда не зашит — иначе латинская картинка провоцирует модель на
# кириллический ответ.
_CAPTCHA_PROMPT_COMMON = (
    "Read the CAPTCHA image. It contains exactly two short words written "
    "along an arc. Return the two words exactly as they are written: do "
    "NOT guess, do NOT repair them, and do NOT add, remove or reorder "
    "letters. Answer with strict JSON only, without any explanations: "
    '{"first_word": "<word>", "second_word": "<word>"}.'
)

# Правила для конкретного алфавита.
_CAPTCHA_PROMPT_RULES = {
    CAPTCHA_SCRIPT_LATIN: (
        " The letters are LATIN (a-z). Copy them exactly as Latin letters; "
        "never turn them into Cyrillic."
    ),
    CAPTCHA_SCRIPT_CYRILLIC: (
        " The letters are Cyrillic (а-я, А-Я). Copy them exactly as "
        "Cyrillic letters; never transliterate them into Latin. "
        "Copy ё as ё."
    ),
    CAPTCHA_SCRIPT_ANY: (
        " The letters may be Latin or Cyrillic; copy them exactly as they "
        "appear."
    ),
}

_CAPTCHA_USER_PROMPT = {
    CAPTCHA_SCRIPT_LATIN: (
        "Read the two Latin words in the image and return JSON with "
        '"first_word" and "second_word".'
    ),
    CAPTCHA_SCRIPT_CYRILLIC: (
        "Read the two Cyrillic words in the image and return JSON with "
        '"first_word" and "second_word".'
    ),
    CAPTCHA_SCRIPT_ANY: (
        "Read the two words in the image and return JSON with "
        '"first_word" and "second_word".'
    ),
}


def captcha_script(language: str | None) -> str:
    """Ожидаемый алфавит ответа для языка картинки.

    Неизвестный язык считаем английским: дефолт
    ``CAPTCHA_LANGUAGE_DEFAULT`` тоже ``en``.
    """
    if language == CAPTCHA_SCRIPT_ANY:
        return CAPTCHA_SCRIPT_ANY
    if (language or "").strip().lower() == "ru":
        return CAPTCHA_SCRIPT_CYRILLIC
    return CAPTCHA_SCRIPT_LATIN


def _foreign_letters(text: str, script: str) -> list[str]:
    """Символы чужого алфавита, которых не должно быть в ответе."""
    if script == CAPTCHA_SCRIPT_LATIN:
        foreign = {
            ch.lower()
            for ch in text
            if "а" <= ch.lower() <= "я" or ch.lower() == "ё"
        }
    elif script == CAPTCHA_SCRIPT_CYRILLIC:
        foreign = {ch.lower() for ch in text if "a" <= ch.lower() <= "z"}
    else:
        # ANY: принимаем оба алфавита.
        return []
    return sorted(foreign)


@dataclass
class ChatOpenAI:
    api_key: str
    _: KW_ONLY
    base_url: str
    system_prompt: str | None = None
    # Базовая история диалога, которая подставляется в каждый запрос
    # перед history из вызова complete()/recognize_text()
    history: list[dict] = field(default_factory=list)
    # Общий таймаут на весь запрос
    timeout: float | None = None
    # Отдельный таймаут только на установку соединения
    connect_timeout: float | None = None

    # Параметры для retry логики
    max_retries: int = 3

    temperature: float = 0.0
    max_completion_tokens: int = 1000
    model: str | None = None

    # Минимальный интервал между запросами (сек). Если с прошлого запроса
    # прошло больше — не ждём.
    delay: float | None = None

    proxy_url: str | None = None

    session: requests.Session = field(default_factory=requests.Session)

    # Внутренние поля
    _previous_request_time: float = field(default=0.0, init=False)
    _lock: Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._lock = Lock()
        self.delay = self.delay or DEFAULT_DELAY
        self.timeout = self.timeout or DEFAULT_TIMEOUT
        self.connect_timeout = self.connect_timeout or DEFAULT_CONNECT_TIMEOUT

    def _default_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
        }

    def _request(self, payload: dict) -> requests.Response:
        """Выполнение запроса с минимальным интервалом между запросами.

        Если с прошлого запроса прошло больше `delay` секунд,
        ожидание не выполняется.
        """
        timeout = Timeout(connect=self.connect_timeout, total=self.timeout)
        headers = self._default_headers()
        proxies = get_proxies_dict(self.proxy_url)

        with self._lock:
            if self._previous_request_time > 0:
                delay = self.delay - (
                    time.monotonic() - self._previous_request_time
                )
                if delay > 0:
                    logger.debug("Wait %.2fs before OpenAI request", delay)
                    time.sleep(delay)

            try:
                return self.session.post(
                    self.base_url,
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                    proxies=proxies,
                )
            finally:
                self._previous_request_time = time.monotonic()

    def _get_retry_delay(
        self, response: requests.Response, attempt: int
    ) -> float:
        """Вычисление задержки перед повторным запросом при 429 ошибке."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(float(retry_after), 1.0)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after).timestamp()
                    return max(retry_at - time.time(), 1.0)
                except (TypeError, ValueError, OverflowError):
                    pass

        return float(attempt + 1)

    def _build_messages(
        self,
        user_content: str | list[dict],
        system_prompt: str | None = None,
        history: list[dict] | None = None,
    ) -> list[dict]:
        """Сборка списка messages:
        system -> self.history -> history -> текущее сообщение.

        Если system_prompt не передан (None), берётся self.system_prompt.
        Пустой системный промпт не добавляется.
        """
        messages: list[dict] = []

        if system_prompt is None:
            system_prompt = self.system_prompt

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # Порядок: system -> история из конструктора -> история из вызова
        # -> текущее сообщение
        if self.history:
            messages.extend(self.history)

        if history:
            messages.extend(history)

        # Пользовательское сообщение всегда обязательно
        messages.append({"role": "user", "content": user_content})
        return messages

    def _post_chat(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_completion_tokens: int | None = None,
        **extra,
    ) -> str:
        """Отправка messages в /chat/completions с retry-логикой.

        Параметры, не переданные явно (None), берутся из полей класса.
        Любые дополнительные параметры (top_p, seed, response_format...)
        добавляются в payload как есть.
        """
        payload = {
            "model": model if model is not None else self.model,
            "messages": messages,
            "temperature": (
                temperature if temperature is not None else self.temperature
            ),
            "max_completion_tokens": (
                max_completion_tokens
                if max_completion_tokens is not None
                else self.max_completion_tokens
            ),
            "stream": False,
            **extra,
        }

        return self._post_payload(payload)

    def _post_payload(self, payload: dict) -> str:
        """Отправка готового payload в /chat/completions с retry-логикой."""
        for attempt in range(self.max_retries + 1):
            try:
                response = self._request(payload)
            except requests.exceptions.RequestException as ex:
                # Таймаут локальной модели и оборванное соединение
                # повторяем, а не роняем отклик на первой вакансии
                if attempt >= self.max_retries:
                    raise OpenAIError(f"Network error: {ex}") from ex

                logger.warning("OpenAI network error, retry: %s", ex)
                continue

            if response.status_code == 429:
                if attempt >= self.max_retries:
                    raise OpenAIError("OpenAI rate limit exceeded")

                delay = self._get_retry_delay(response, attempt)
                logger.warning(
                    "OpenAI returned 429 Too Many Requests, retry in %.2fs",
                    delay,
                )
                time.sleep(delay)
                continue

            try:
                response.raise_for_status()
                data = response.json()
            except requests.exceptions.RequestException as ex:
                raise OpenAIError(f"Network error: {ex}") from ex
            except ValueError as ex:
                raise OpenAIError(f"Invalid JSON response: {ex}") from ex

            if "error" in data:
                raise OpenAIError(data["error"]["message"])

            try:
                assistant_message = data["choices"][0]["message"]["content"]
                return (
                    assistant_message if assistant_message is not None else ""
                )
            except (KeyError, IndexError) as ex:
                raise OpenAIError(f"Invalid response format: {ex}") from ex

        raise OpenAIError("OpenAI request failed after retries")

    def complete(
        self,
        message: str,
        *,
        history: list[dict] | None = None,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_completion_tokens: int | None = None,
        **extra,
    ) -> str:
        """Генерация текста через OpenAI API.

        history — предыдущие сообщения диалога в формате
        [{"role": "user" | "assistant", "content": "..."}, ...].
        Добавляется после self.history (истории из конструктора).
        """
        messages = self._build_messages(message, system_prompt, history)

        logger.debug(
            "AI системный промпт: %.300s",
            system_prompt if system_prompt is not None else self.system_prompt,
        )
        logger.debug("AI запрос: %.300s", message)

        return self._post_chat(
            messages,
            model=model,
            temperature=temperature,
            max_completion_tokens=max_completion_tokens,
            **extra,
        )

    def recognize_text(
        self,
        data: bytes,
        *,
        mime_type: str = "image/png",
        prompt: str = (
            "Распознай текст на изображении. "
            "Верни только распознанный текст без пояснений."
        ),
        history: list[dict] | None = None,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_completion_tokens: int | None = None,
        **extra,
    ) -> str:
        """Распознавание текста на изображении через vision-модель."""
        if not data:
            raise OpenAIError("Empty image data")

        b64 = base64.b64encode(data).decode("ascii")

        messages = self._build_messages(
            [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{b64}",
                    },
                },
            ],
            system_prompt,
            history,
        )

        logger.debug("AI vision запрос: %s, %d байт", mime_type, len(data))

        return self._post_chat(
            messages,
            model=model,
            temperature=temperature,
            max_completion_tokens=max_completion_tokens,
            **extra,
        )

    # --- Распознавание капчи -------------------------------------------

    CAPTCHA_PROMPT_COMMON = _CAPTCHA_PROMPT_COMMON
    CAPTCHA_PROMPT_RULES = _CAPTCHA_PROMPT_RULES
    CAPTCHA_USER_PROMPT = _CAPTCHA_USER_PROMPT

    def _captcha_payload(
        self,
        b64: str,
        mime_type: str,
        temperature: float,
        script: str,
    ) -> dict:
        """Payload запроса к vision-модели с промптом нужного алфавита."""
        payload: dict = {
            "temperature": temperature,
            "max_completion_tokens": 100,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        self.CAPTCHA_PROMPT_COMMON
                        + self.CAPTCHA_PROMPT_RULES[script]
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{b64}",
                                "detail": "high",
                            },
                        },
                        {
                            "type": "text",
                            "text": self.CAPTCHA_USER_PROMPT[script],
                        },
                    ],
                },
            ],
        }
        model = getattr(self, "model", None)
        if model is not None:
            payload["model"] = model
        return payload

    def read_captcha(
        self,
        data: bytes,
        *,
        script: str = CAPTCHA_SCRIPT_ANY,
        mime_type: str = "image/png",
        temperature: float | None = None,
    ) -> str:
        """Распознаёт капчу. Возвращает два слова через пробел."""
        if not data:
            raise OpenAIError("Empty image data")

        b64 = base64.b64encode(data).decode("ascii")
        payload = self._captcha_payload(
            b64,
            mime_type,
            self.temperature if temperature is None else temperature,
            script,
        )

        logger.debug("AI captcha запрос: %s, %d байт", mime_type, len(data))

        raw = self._post_payload(payload)
        return self._parse_captcha_json(raw, script)

    @staticmethod
    def _parse_captcha_json(raw: str, script: str) -> str:
        """Разбирает JSON-ответ модели и проверяет алфавит."""
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise OpenAIError(
                "В ответе модели нет JSON с распознанным текстом"
            )

        try:
            data = json.loads(match.group(0))
        except ValueError as ex:
            raise OpenAIError(
                f"Не удалось разобрать JSON ответа модели: {ex}"
            ) from ex

        if not isinstance(data, dict):
            raise OpenAIError("В ответе модели нет объекта JSON")

        first = str(data.get("first_word") or "").strip()
        second = str(data.get("second_word") or "").strip()
        if not first or not second:
            raise OpenAIError("Модель распознала не оба слова")

        for word in (first, second):
            foreign = _foreign_letters(word, script)
            if foreign:
                raise OpenAIError(
                    "Ответ содержит символы не того алфавита: "
                    + "".join(foreign)
                )

        return f"{first} {second}".lower()
