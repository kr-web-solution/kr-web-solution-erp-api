"""PDF engine errors — map to HTTP in the router without leaking internals."""

from __future__ import annotations


class PdfEngineError(Exception):
    """Base class for PDF engine failures."""

    def __init__(self, message: str = "PDF engine error") -> None:
        self.message = message
        super().__init__(message)


class TemplateNotFoundError(PdfEngineError):
    def __init__(self, design_key: str) -> None:
        super().__init__(f"Template not found: {design_key}")
        self.design_key = design_key


class RenderError(PdfEngineError):
    def __init__(self, message: str = "PDF rendering failed") -> None:
        super().__init__(message)


class PdfOperationError(PdfEngineError):
    def __init__(self, message: str = "PDF operation failed") -> None:
        super().__init__(message)


class AssetAccessError(PdfEngineError):
    def __init__(self, message: str = "Asset access denied") -> None:
        super().__init__(message)


class PreviewValidationError(PdfEngineError):
    def __init__(self, message: str = "Invalid preview payload") -> None:
        super().__init__(message)


class GenerationBusyError(PdfEngineError):
    def __init__(self, message: str = "PDF generation capacity exceeded") -> None:
        super().__init__(message)


class GenerationTimeoutError(PdfEngineError):
    def __init__(self, message: str = "PDF generation timed out") -> None:
        super().__init__(message)
