class Error(Exception):
    pass


# Используй эту ошибку для прерывания выполнения команд
class OperationError(Error):
    pass


class NotAuthorizedError(OperationError):
    """Нет авторизованной веб-сессии hh.ru (cookies.txt отсутствует/истёк)."""
    pass


class OperationCanceled(OperationError):
    pass
