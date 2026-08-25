"""关系网 API 请求模型"""
from pydantic import BaseModel


class RelationshipUpdate(BaseModel):
    relation_type: str | None = None
    is_partner: bool | None = None
    relationship_summary: str | None = None
