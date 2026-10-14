"""Регрессия: пустой/битый cookies.txt не должен ронять утилиту.

Раньше `session` без проверки звал `MozillaCookieJar.load()` для любого
существующего cookies.txt. Пустой (0 байт) файл вызывает
`http.cookiejar.LoadError: ... does not look like a Netscape format
cookies file`, и утилита падала ещё до обращения к hh.ru (в том числе
на команде whoami).
"""

from __future__ import annotations

from pathlib import Path

from hh_applicant_tool.tool import HHApplicantTool


def test_empty_cookies_file_is_removed_and_session_builds(tmp_path: Path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_bytes(b"")  # 0 байт

    tool = HHApplicantTool(config_dir=tmp_path)

    session = tool.session  # не должно кидать LoadError

    assert session is not None
    assert not cookies.exists()  # пустой файл самоудаляется


def test_invalid_cookies_file_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "cookies.txt").write_text(
        "this is not a netscape cookies file", encoding="utf-8"
    )

    tool = HHApplicantTool(config_dir=tmp_path)

    session = tool.session  # не должно кидать LoadError

    assert session is not None
