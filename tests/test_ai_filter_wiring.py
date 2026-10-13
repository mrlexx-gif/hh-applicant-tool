"""Регрессия: AI-фильтр вакансий должен отвечать строго да/нет.

Ранее в качестве системного промпта фильтра ошибочно подставлялся
шаблон данных ``DEFAULT_AI_FILTER_PROMPT`` (со списком навыков резюме и
плейсхолдерами ``%(resume_title)s`` / ``%(resume_skills)s``).
В итоге модель не знала, что от неё ждут ответ «да»/«нет», отвечала
произвольным текстом, а ``_parse_is_suitable_response`` возвращал None —
отсюда предупреждение [W] «непонятный ответ AI-фильтра».
"""

from __future__ import annotations

import pytest

from hh_applicant_tool.operations.apply_vacancies import (
    DEFAULT_AI_FILTER_PROMPT,
    DEFAULT_AI_FILTER_SYSTEM_PROMPT,
    Operation,
)


def test_system_prompt_requires_yes_no() -> None:
    lowered = DEFAULT_AI_FILTER_SYSTEM_PROMPT.lower()
    assert "да" in lowered
    assert "нет" in lowered


def test_system_prompt_is_not_the_data_template() -> None:
    assert DEFAULT_AI_FILTER_SYSTEM_PROMPT != DEFAULT_AI_FILTER_PROMPT
    # Системный промпт — обычная строка, без шаблонных плейсхолдеров.
    assert "%(" not in DEFAULT_AI_FILTER_SYSTEM_PROMPT


def test_user_prompt_template_contains_resume_data() -> None:
    # Данные резюме должны передаваться именно в пользовательском промпте.
    assert "%(resume_title)s" in DEFAULT_AI_FILTER_PROMPT
    assert "%(resume_skills)s" in DEFAULT_AI_FILTER_PROMPT


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("да", True),
        ("Да, подходит.", True),
        ("yes", True),
        ("TRUE", True),
        ("нет", False),
        ("Нет, не подходит.", False),
        ("no", False),
        ("false", False),
        ("возможно", None),
        ("", None),
    ],
)
def test_parse_is_suitable_response(response: str, expected) -> None:
    assert Operation._parse_is_suitable_response(response) is expected
