"""Collection management service."""
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
import sqlite3
import json

from api.models.database import get_db_connection, init_db
from api.models.schemas import CollectionItemCreate, BinderCreate, BinderUpdate

logger = logging.getLogger(__name__)


class CollectionService:
    """Manages the user's Pokemon card collection."""

    def __init__(self):
        init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        return conn

    def add_card(self, item: CollectionItemCreate, user_id: int = 1) -> Dict[str, Any]:
        """Add a card to the collection."""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO collection_items 
                   (card_id, user_id, condition, language, quantity, notes, 
                    is_reverse_holo, is_first_edition, scan_image_path, date_acquired)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (item.card_id, user_id, item.condition, item.language, item.quantity,
                 item.notes, item.is_reverse_holo, item.is_first_edition,
                 item.scan_image_path, datetime.utcnow().strftime('%Y-%m-%d'))
            )
            conn.commit()
            item_id = cursor.lastrowid
            return self.get_item(item_id, user_id)
        finally:
            conn.close()

    def add_cards_batch(self, items: List[CollectionItemCreate], user_id: int = 1) -> List[Dict[str, Any]]:
        """Add multiple cards to the collection."""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            saved_items = []
            for item in items:
                cursor.execute(
                    """INSERT INTO collection_items 
                       (card_id, user_id, condition, language, quantity, notes,
                        is_reverse_holo, is_first_edition, scan_image_path, date_acquired)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.card_id, user_id, item.condition, item.language, item.quantity,
                     item.notes, item.is_reverse_holo, item.is_first_edition,
                     item.scan_image_path, datetime.utcnow().strftime('%Y-%m-%d'))
                )
                saved_items.append(self.get_item(cursor.lastrowid, user_id))
            conn.commit()
            return saved_items
        except Exception as e:
            conn.rollback()
            logger.error(f"Batch add error: {e}")
            raise
        finally:
            conn.close()

    def get_all_items(self, limit: int = 100, offset: int = 0, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get all collection items with card details."""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            if user_id:
                cursor.execute(
                    """SELECT ci.*, c.name as card_name, c.ptcg_id, c.number, c.rarity, 
                               c.set_name, c.images, c.hp, c.types
                        FROM collection_items ci
                        LEFT JOIN cards c ON ci.card_id = c.id
                        WHERE ci.user_id = ?
                        ORDER BY ci.added_at DESC
                        LIMIT ? OFFSET ?""",
                    (user_id, limit, offset)
                )
            else:
                cursor.execute(
                    """SELECT ci.*, c.name as card_name, c.ptcg_id, c.number, c.rarity,
                               c.set_name, c.images, c.hp, c.types
                        FROM collection_items ci
                        LEFT JOIN cards c ON ci.card_id = c.id
                        ORDER BY ci.added_at DESC
                        LIMIT ? OFFSET ?""",
                    (limit, offset)
                )
            
            rows = cursor.fetchall()
            result = []
            for row in rows:
                images = None
                if row['images']:
                    try:
                        images = json.loads(row['images'])
                    except:
                        images = None
                
                types = None
                if row['types']:
                    try:
                        types = json.loads(row['types'])
                    except:
                        types = None
                
                result.append({
                    "id": row["id"],
                    "card_id": row["card_id"],
                    "user_id": row["user_id"],
                    "condition": row["condition"],
                    "language": row["language"],
                    "quantity": row["quantity"],
                    "notes": row["notes"],
                    "is_reverse_holo": bool(row["is_reverse_holo"]),
                    "is_first_edition": bool(row["is_first_edition"]),
                    "scan_image_path": row["scan_image_path"],
                    "date_acquired": row["date_acquired"],
                    "added_at": row["added_at"],
                    "card": {
                        "id": row["id"],
                        "ptcg_id": row["ptcg_id"],
                        "name": row["card_name"] or "Unknown",
                        "number": row["number"],
                        "rarity": row["rarity"],
                        "set_name": row["set_name"],
                        "hp": row["hp"],
                        "types": types,
                        "images": images,
                    } if row["card_name"] else None,
                })
            return result
        finally:
            conn.close()

    def get_item(self, item_id: int, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Get a single collection item."""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            if user_id:
                cursor.execute(
                    """SELECT ci.*, c.name as card_name, c.ptcg_id, c.number, c.rarity,
                               c.set_name, c.images, c.hp, c.types
                        FROM collection_items ci
                        LEFT JOIN cards c ON ci.card_id = c.id
                        WHERE ci.id = ? AND ci.user_id = ?""",
                    (item_id, user_id)
                )
            else:
                cursor.execute(
                    """SELECT ci.*, c.name as card_name, c.ptcg_id, c.number, c.rarity,
                               c.set_name, c.images, c.hp, c.types
                        FROM collection_items ci
                        LEFT JOIN cards c ON ci.card_id = c.id
                        WHERE ci.id = ?""",
                    (item_id,)
                )
            
            row = cursor.fetchone()
            if not row:
                return None
            
            images = None
            if row['images']:
                try:
                    images = json.loads(row['images'])
                except:
                    images = None
            
            types = None
            if row['types']:
                try:
                    types = json.loads(row['types'])
                except:
                    types = None
            
            return {
                "id": row["id"],
                "card_id": row["card_id"],
                "user_id": row["user_id"],
                "condition": row["condition"],
                "language": row["language"],
                "quantity": row["quantity"],
                "notes": row["notes"],
                "is_reverse_holo": bool(row["is_reverse_holo"]),
                "is_first_edition": bool(row["is_first_edition"]),
                "scan_image_path": row["scan_image_path"],
                "date_acquired": row["date_acquired"],
                "card": {
                    "ptcg_id": row["ptcg_id"],
                    "name": row["card_name"] or "Unknown",
                    "number": row["number"],
                    "rarity": row["rarity"],
                    "set_name": row["set_name"],
                    "hp": row["hp"],
                    "types": types,
                    "images": images,
                } if row["card_name"] else None,
            }
        finally:
            conn.close()

    def delete_item(self, item_id: int, user_id: Optional[int] = None) -> bool:
        """Remove a card from the collection."""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            if user_id:
                cursor.execute("DELETE FROM collection_items WHERE id = ? AND user_id = ?", (item_id, user_id))
            else:
                cursor.execute("DELETE FROM collection_items WHERE id = ?", (item_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def get_stats(self, user_id: Optional[int] = None) -> Dict[str, Any]:
        """Get collection statistics."""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            
            # Total items count
            if user_id:
                cursor.execute("SELECT COUNT(*) FROM collection_items WHERE user_id = ?", (user_id,))
            else:
                cursor.execute("SELECT COUNT(*) FROM collection_items")
            total_items = cursor.fetchone()[0]

            # Total cards (sum of quantities)
            if user_id:
                cursor.execute("SELECT SUM(quantity) FROM collection_items WHERE user_id = ?", (user_id,))
            else:
                cursor.execute("SELECT SUM(quantity) FROM collection_items")
            total_cards = cursor.fetchone()[0] or 0

            # Unique cards count
            if user_id:
                cursor.execute("SELECT COUNT(DISTINCT card_id) FROM collection_items WHERE user_id = ?", (user_id,))
            else:
                cursor.execute("SELECT COUNT(DISTINCT card_id) FROM collection_items")
            unique_cards = cursor.fetchone()[0] or 0

            # By set
            cursor.execute("""
                SELECT s.name, COUNT(ci.id)
                FROM collection_items ci
                JOIN cards c ON ci.card_id = c.id
                JOIN sets s ON c.set_id = s.id
                GROUP BY s.name
            """)
            by_set = [{"set": row[0] or "Unknown", "count": row[1]} for row in cursor.fetchall()]

            return {
                "total_items": total_items,
                "total_cards": total_cards,
                "unique_cards": unique_cards,
                "by_set": by_set,
            }
        finally:
            conn.close()

    def get_or_create_card(self, card_id: str, name: str, set_code: str,
                           set_name: str = "", number: str = "", rarity: str = "",
                           hp: int = 0, types: List[str] = None,
                           image_url: str = "") -> Optional[int]:
        """Find or create a card in the database from TCGdex data."""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            
            # Check if card exists
            cursor.execute("SELECT id FROM cards WHERE ptcg_id = ?", (card_id,))
            existing = cursor.fetchone()
            if existing:
                return existing[0]

            # Find or create set
            cursor.execute("SELECT id FROM sets WHERE ptcg_id = ?", (set_code,))
            set_row = cursor.fetchone()
            if set_row:
                set_id = set_row[0]
            else:
                cursor.execute("INSERT INTO sets (ptcg_id, name, series, total_cards) VALUES (?, ?, ?, ?)",
                             (set_code, set_name, "", 0))
                set_id = cursor.lastrowid

            # Create card
            cursor.execute("""INSERT INTO cards 
                           (ptcg_id, name, number, rarity, set_id, hp, types, artist, rarity_code, images, legal, set_name)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                          (card_id, name, number, rarity, set_id, str(hp) if hp else None,
                           json.dumps(types or []), "", "", json.dumps({"small": image_url, "large": image_url}) if image_url else None,
                           json.dumps({}), set_name))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            conn.rollback()
            logger.error(f"Error creating card: {e}")
            return None
        finally:
            conn.close()

    # ==================== Binder Methods ====================
    
    def create_binder(self, name: str, description: str, user_id: int) -> Dict[str, Any]:
        """Create a new binder for the user."""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO binders (name, description, user_id, is_active) VALUES (?, ?, ?, ?)",
                (name, description, user_id, True)
            )
            binder_id = cursor.lastrowid
            conn.commit()
            return self.get_binder(binder_id, user_id)
        except Exception as e:
            conn.rollback()
            logger.error(f"Create binder error: {e}")
            raise
        finally:
            conn.close()

    def get_binders(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all binders for a user with item counts (single query)."""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT b.*, COUNT(bi.id) as item_count
                FROM binders b
                LEFT JOIN binder_items bi ON b.id = bi.binder_id
                WHERE b.user_id = ?
                GROUP BY b.id
                ORDER BY b.created_at DESC
                """,
                (user_id,)
            )
            binders = cursor.fetchall()
            
            result = []
            for binder in binders:
                result.append({
                    "id": binder["id"],
                    "name": binder["name"],
                    "description": binder["description"],
                    "user_id": binder["user_id"],
                    "is_active": bool(binder["is_active"]),
                    "created_at": binder["created_at"],
                    "updated_at": binder["updated_at"],
                    "item_count": binder["item_count"],
                    "items": []
                })
            return result
        finally:
            conn.close()

    def get_binder(self, binder_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific binder with its items."""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM binders WHERE id = ? AND user_id = ?",
                (binder_id, user_id)
            )
            binder = cursor.fetchone()
            if not binder:
                return None
            
            cursor.execute(
                """SELECT bi.*, c.name as card_name, ci.condition
                   FROM binder_items bi
                   JOIN collection_items ci ON bi.collection_item_id = ci.id
                   JOIN cards c ON ci.card_id = c.id
                   WHERE bi.binder_id = ?
                   ORDER BY bi.position""",
                (binder_id,)
            )
            items = cursor.fetchall()
            
            item_list = []
            for item in items:
                item_list.append({
                    "id": item["id"],
                    "collection_item_id": item["collection_item_id"],
                    "position": item["position"],
                    "card_name": item["card_name"],
                    "condition": item["condition"],
                })
            
            return {
                "id": binder["id"],
                "name": binder["name"],
                "description": binder["description"],
                "user_id": binder["user_id"],
                "is_active": bool(binder["is_active"]),
                "created_at": binder["created_at"],
                "updated_at": binder["updated_at"],
                "item_count": len(item_list),
                "items": item_list
            }
        finally:
            conn.close()

    def update_binder(self, binder_id: int, user_id: int, name: Optional[str] = None,
                      description: Optional[str] = None, item_ids: Optional[List[int]] = None) -> Optional[Dict[str, Any]]:
        """Update a binder (name, description, or items)."""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            
            # Get binder
            cursor.execute(
                "SELECT * FROM binders WHERE id = ? AND user_id = ?",
                (binder_id, user_id)
            )
            binder = cursor.fetchone()
            if not binder:
                return None
            
            # Update fields
            if name is not None:
                cursor.execute("UPDATE binders SET name = ? WHERE id = ?", (name, binder_id))
            if description is not None:
                cursor.execute("UPDATE binders SET description = ? WHERE id = ?", (description, binder_id))
            cursor.execute("UPDATE binders SET updated_at = ? WHERE id = ?", 
                          (datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'), binder_id))
            
            if item_ids is not None:
                # Clear existing items
                cursor.execute("DELETE FROM binder_items WHERE binder_id = ?", (binder_id,))
                
                # Add new items
                for idx, item_id in enumerate(item_ids):
                    cursor.execute(
                        "SELECT id FROM collection_items WHERE id = ? AND user_id = ?",
                        (item_id, user_id)
                    )
                    if cursor.fetchone():
                        cursor.execute(
                            "INSERT INTO binder_items (binder_id, collection_item_id, position) VALUES (?, ?, ?)",
                            (binder_id, item_id, idx)
                        )
            
            conn.commit()
            return self.get_binder(binder_id, user_id)
        except Exception as e:
            conn.rollback()
            logger.error(f"Update binder error: {e}")
            raise
        finally:
            conn.close()

    def delete_binder(self, binder_id: int, user_id: int) -> bool:
        """Delete a binder."""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM binder_items WHERE binder_id = ?",
                (binder_id,)
            )
            cursor.execute(
                "DELETE FROM binders WHERE id = ? AND user_id = ?",
                (binder_id, user_id)
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            conn.rollback()
            logger.error(f"Delete binder error: {e}")
            raise
        finally:
            conn.close()