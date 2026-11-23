import logging
import sys
from .config import settings

def configure_logging() -> None:
    level = logging.INFO if settings.PRODUCTION else logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )
    # Enable all access and application loggers
    for name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "kr.api",
        "kr.pdf",
        "fastapi",
    ):
        lg = logging.getLogger(name)
        lg.setLevel(level)
        lg.disabled = False


