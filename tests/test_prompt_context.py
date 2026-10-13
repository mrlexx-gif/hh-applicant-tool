"""Регрессия: CLI-флаги промптов и загрузка контекста (*.md).

После слияния с upstream перестали работать (сломались) механизмы:
- ``--system-prompt`` / ``--message-prompt`` (алиасы новых флагов);
- ``--context-dir``: файлы ``*.md`` не подмешивались в системный промпт.

Тесты фиксируют разбор алиасов и загрузку пользовательского контекста.
"""

from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path

from hh_applicant_tool.operations.apply_vacancies import Operation


def _parser() -> ArgumentParser:
    parser = ArgumentParser()
    Operation().setup_parser(parser)
    return parser


def test_system_and_message_prompt_aliases() -> None:
    args = _parser().parse_args(
        ["--system-prompt", "SYS", "--message-prompt", "MSG"]
    )
    assert args.cover_letter_system_prompt == "SYS"
    assert args.cover_letter_prompt == "MSG"


def test_context_dir_flag_is_parsed() -> None:
    args = _parser().parse_args(["--context-dir", "./ctx"])
    assert args.context_dir == Path("./ctx")


def test_load_user_context_reads_md_files_sorted(tmp_path: Path) -> None:
    (tmp_path / "b.md").write_text("B content", encoding="utf-8")
    (tmp_path / "a.md").write_text("A content", encoding="utf-8")
    (tmp_path / "ignore.txt").write_text("nope", encoding="utf-8")

    context = Operation()._load_user_context(tmp_path)

    assert "--- a.md ---\nA content" in context
    assert "--- b.md ---\nB content" in context
    assert "nope" not in context
    assert context.index("a.md") < context.index("b.md")


def test_load_user_context_without_dir_returns_empty(tmp_path: Path) -> None:
    assert Operation()._load_user_context(None) == ""
    assert Operation()._load_user_context(tmp_path / "missing") == ""
