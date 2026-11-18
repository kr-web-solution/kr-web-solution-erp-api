from typing import Any, Optional
from pydantic import BaseModel, ConfigDict

class AppBaseSchema(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

class MessageResponse(AppBaseSchema):
    detail: str

class ErrorResponse(AppBaseSchema):
    detail: str
    extra: Optional[dict[str, Any]] = None

