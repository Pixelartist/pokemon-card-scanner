"""Database model for Binders."""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, JSON, Text
from sqlalchemy.orm import relationship
from datetime import datetime
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Import from database module
from api.models.database import Base, SessionLocal


class Binder(Base):
    """User's personal binder for organizing cards."""
    __tablename__ = "binders"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Many-to-many relationship via binder_items table
    items = relationship("BinderItem", back_populates="binder", cascade="all, delete-orphan")


class BinderItem(Base):
    """Items in a binder."""
    __tablename__ = "binder_items"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    binder_id = Column(Integer, ForeignKey("binders.id", ondelete="CASCADE"), nullable=False)
    collection_item_id = Column(Integer, ForeignKey("collection_items.id", ondelete="CASCADE"), nullable=False)
    position = Column(Integer, default=0)  # Position in binder
    
    binder = relationship("Binder", back_populates="items")
    collection_item = relationship("CollectionItem", back_populates="binder_items")


def init_binder_db():
    """Initialize binder tables."""
    from api.models.database import engine
    Base.metadata.create_all(bind=engine)
