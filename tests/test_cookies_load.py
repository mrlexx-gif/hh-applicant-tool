"""Регрессия: пустой/битый cookies.txt не должен ронять утилиту.

Раньше `session` без проверки звал `MozillaCookieJar.load()` для любого
существующего cookies.txt. Пустой (0 байт) файл вызывает
`http.cookiejar.LoadError: ... does not look like a Netscape format
cookies file`, и утилита падала ещё до обращения к hh.ru (в том числе
на команде whoami).
"""

from __future__ import annotations

from http.cookiejar import Cookie
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


def _hh_cookie(name: str, value: str) -> Cookie:
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=".hh.ru",
        domain_specified=True,
        domain_initial_dot=True,
        path="/",
        path_specified=True,
        secure=False,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )


def _tool_with_cookie(tmp_path: Path) -> HHApplicantTool:
    tool = HHApplicantTool(config_dir=tmp_path)
    tool.session.cookies.set_cookie(_hh_cookie("_xsrf", "TOKEN"))
    # Провоцируем ветку сохранения (куки отличаются от исходных).
    tool._original_cookies = []
    return tool


def test_save_cookies_is_atomic_and_leaves_no_temp_file(tmp_path: Path) -> None:
    tool = _tool_with_cookie(tmp_path)

    tool.save_cookies()

    cookies = tmp_path / "cookies.txt"
    assert cookies.exists()
    assert cookies.stat().st_size > 0  # не пустой файл
    assert not (tmp_path / "cookies.txt.tmp").exists()


def test_save_cookies_keeps_existing_file_when_write_fails(
    tmp_path: Path, monkeypatch
) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("OLD CONTENT", encoding="utf-8")

    tool = _tool_with_cookie(tmp_path)

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    # Раньше прямая запись открывала файл на усечение ("w") и при сбое
    # оставляла 0-байтовый cookies.txt. Теперь запись атомарная: при
    # ошибке старый файл должен остаться целым.
    monkeypatch.setattr("os.replace", boom)

    tool.save_cookies()

    assert cookies.read_text(encoding="utf-8") == "OLD CONTENT"
    assert not (tmp_path / "cookies.txt.tmp").exists()
