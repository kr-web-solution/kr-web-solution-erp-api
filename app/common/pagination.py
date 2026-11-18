from typing import Generic, TypeVar, List
from pydantic import BaseModel, Field

T = TypeVar("T")

class PaginationParams(BaseModel):
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)

class Page(BaseModel, Generic[T]):
    items: List[T]
    total: int
    skip: int
    limit: int

