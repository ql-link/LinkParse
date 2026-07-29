class LinkParseError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class EngineUnavailable(LinkParseError):
    def __init__(self, engine: str, detail: str | None = None) -> None:
        message = f"Engine '{engine}' is unavailable"
        if detail:
            message = f"{message}: {detail}"
        super().__init__("ENGINE_UNAVAILABLE", message, 503)
