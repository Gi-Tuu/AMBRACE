"""宠物请求/响应模型"""
from datetime import datetime
from pydantic import BaseModel


class PetResponse(BaseModel):
    id: int
    name: str
    species: str
    species_label: str = ""
    avatar_url: str | None = None
    level: int = 1
    exp: int = 0
    hunger: int = 80
    mood: int = 80
    energy: int = 80
    cleanliness: int = 80
    status_text: str = ""
    need_attention: bool = False
    is_special: bool = False
    created_at: datetime


class PetListResponse(BaseModel):
    pets: list[PetResponse]
    total: int


class CreatePetRequest(BaseModel):
    species: str
    name: str


class RenamePetRequest(BaseModel):
    name: str
