"""Pydantic schemas for Pokemon Card Scanner."""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


class SetSchema(BaseModel):
    id: Optional[int] = None
    ptcg_id: str
    name: str
    series: Optional[str] = None
    release_date: Optional[str] = None
    total_cards: Optional[int] = None
    images: Optional[Dict[str, str]] = None


class CardSchema(BaseModel):
    id: Optional[int] = None
    ptcg_id: str
    name: str
    number: Optional[str] = None
    rarity: Optional[str] = None
    set_id: Optional[int] = None
    hp: Optional[str] = None
    types: Optional[List[str]] = None
    artist: Optional[str] = None
    rarity_code: Optional[str] = None
    images: Optional[Dict[str, str]] = None
    legal: Optional[Dict[str, Any]] = None
    set_name: Optional[str] = None


class CollectionItemSchema(BaseModel):
    id: Optional[int] = None
    card_id: int
    condition: str = "Near Mint"
    language: str = "EN"
    quantity: int = 1
    notes: Optional[str] = None
    is_reverse_holo: bool = False
    is_first_edition: bool = False
    date_acquired: Optional[datetime] = None
    scan_image_path: Optional[str] = None


class CollectionItemCreate(BaseModel):
    card_id: int
    condition: str = "Near Mint"
    language: str = "EN"
    quantity: int = 1
    notes: Optional[str] = None
    is_reverse_holo: bool = False
    is_first_edition: bool = False
    scan_image_path: Optional[str] = None


class BatchCollectionCreate(BaseModel):
    items: List[CollectionItemCreate]


class UserRegister(BaseModel):
    username: str
    email: str
    password: str
    full_name: Optional[str] = None


class BinderCreate(BaseModel):
    name: str
    description: Optional[str] = None


class BinderUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    item_ids: Optional[List[int]] = None  # Collection item IDs to add to binder


class BinderItemSchema(BaseModel):
    id: int
    collection_item_id: int
    position: int
    card_name: Optional[str] = None
    condition: Optional[str] = None

    class Config:
        from_attributes = True


class BinderSchema(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    user_id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    item_count: int = 0
    items: List[BinderItemSchema] = []

    class Config:
        from_attributes = True


class UserLogin(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    full_name: Optional[str] = None
    created_at: datetime


class AuthToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class ScanRequest(BaseModel):
    image: str  # base64 encoded image
    set_hint: Optional[str] = None
    region: Optional[str] = None


class ScanResult(BaseModel):
    decision: str  # match, ambiguous, no_match
    candidates: List[Dict[str, Any]] = []
    confidence: Optional[float] = None
    message: Optional[str] = None