class Error(Exception):
    pass


class AuthError(Error):
    pass


class InvalidTokenError(Error):
    pass


class TimeOutError(Error):
    pass


class ServerError(Error):
    pass


class UnknownError(Error):
    pass
