"""Material availability service layer."""

from decimal import Decimal
from typing import Optional

from app.extensions import db
from app.core.exceptions import NotFoundError, ValidationError, DuplicateError
from .models import Material, PurchaseOrder


class MaterialService:

    @staticmethod
    def list_materials(active_only: bool = True, below_reorder: bool = False) -> list[Material]:
        q = Material.query
        if active_only:
            q = q.filter_by(is_active=True)
        materials = q.order_by(Material.code).all()
        if below_reorder:
            materials = [m for m in materials if m.is_below_reorder_point]
        return materials

    @staticmethod
    def get_material(material_id: int) -> Material:
        m = Material.query.get(material_id)
        if not m:
            raise NotFoundError(f"Material ID {material_id} not found.")
        return m

    @staticmethod
    def create_material(data: dict) -> Material:
        if Material.query.filter_by(code=data.get("code", "")).first():
            raise DuplicateError(f"Material code '{data['code']}' already exists.")
        m = Material(**data)
        db.session.add(m)
        db.session.commit()
        return m

    @staticmethod
    def update_stock(material_id: int, quantity_delta: Decimal, reason: str = "") -> Material:
        """Adjust stock on hand by `quantity_delta` (positive = receipt, negative = issue)."""
        m = MaterialService.get_material(material_id)
        new_qty = Decimal(str(m.stock_on_hand)) + quantity_delta
        if new_qty < 0:
            raise ValidationError(f"Adjustment would result in negative stock ({new_qty}).")
        m.stock_on_hand = new_qty
        db.session.commit()
        return m

    @staticmethod
    def get_availability_alerts() -> list[Material]:
        """Return materials at or below their reorder point."""
        return [m for m in Material.query.filter_by(is_active=True).all() if m.is_below_reorder_point]

    @staticmethod
    def check_availability(material_code: str, required_qty: Decimal) -> dict:
        """
        Check if enough stock is available for a required quantity.

        Considers stock on hand plus confirmed open purchase orders.
        """
        m = Material.query.filter_by(code=material_code, is_active=True).first()
        if not m:
            return {"available": False, "reason": f"Material '{material_code}' not found."}

        open_pos = PurchaseOrder.query.filter_by(
            material_id=m.id, status=PurchaseOrder.STATUS_OPEN
        ).all()
        inbound_qty = sum(po.quantity_outstanding for po in open_pos)
        total_available = Decimal(str(m.stock_on_hand)) + inbound_qty

        return {
            "material_code": material_code,
            "stock_on_hand": float(m.stock_on_hand),
            "inbound_qty": float(inbound_qty),
            "total_available": float(total_available),
            "required_qty": float(required_qty),
            "available": total_available >= required_qty,
            "shortfall": float(max(Decimal("0"), required_qty - total_available)),
        }
