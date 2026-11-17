class ServiceError(Exception):
    """Error de negocio. Las rutas REST lo traducen a HTTPException con
    `status_code`; las tools MCP lo devuelven como mensaje de error."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class NotFoundError(ServiceError):
    def __init__(self, message: str):
        super().__init__(message, 404)


class ConflictError(ServiceError):
    def __init__(self, message: str):
        super().__init__(message, 409)
