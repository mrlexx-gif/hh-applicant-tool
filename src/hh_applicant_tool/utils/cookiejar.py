import re
from http.cookiejar import Cookie, CookieJar, MozillaCookieJar
from typing import Any


class HHOnlyCookieJar(MozillaCookieJar):
    """Хранилище, которое сохраняет куки только с хх"""

    def set_cookie(self, cookie: Cookie):
        # Регулярное выражение для проверки доменов hh.ru, hh.kz, hh.uz и т.д.
        pattern = r"^(?!israel\.)(?:.*?\.)?hh\.(ru|kz|uz|by|net|com)\.?$"

        if re.search(pattern, cookie.domain):
            super().set_cookie(cookie)


def add_cookies(jar: CookieJar, cookies: list[dict[str, Any]]) -> None:
    """Добавляет куки из Playwright (список словарей) в {CookieJar}.

    Стандартный {CookieJar} не имеет метода set(), поэтому для каждой куки
    создаётся объект {Cookie} и добавляется через set_cookie().
    """
    for c in cookies:
        domain = c.get("domain") or ""
        cookie = Cookie(
            version=0,
            name=c["name"],
            value=c["value"],
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=bool(domain),
            domain_initial_dot=domain.startswith("."),
            path=c.get("path") or "/",
            path_specified=bool(c.get("path")),
            secure=bool(c.get("secure", False)),
            expires=int(c.get("expires") or 0),
            discard=False,
            comment=None,
            comment_url=None,
            rest={"HttpOnly": str(c.get("httpOnly", False))},
            rfc2109=False,
        )
        jar.set_cookie(cookie)
