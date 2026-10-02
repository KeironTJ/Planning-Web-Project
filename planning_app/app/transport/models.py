"""Current transport schedules from CSGTransportWB (not status-change history).

Epicor dashboard mapping:
Key1 = Transport; Character02 = Route; Character08 = Vehicle;
Number05 = Capacity; Number06 = Remaining; ShortChar04 = capacity UOM;
Calculated_Percentage = % Available (NOT workflow completion);
Calculated_OrderQty / OrderVal = order quantity / value;
Date01 / Date02 / Date03 = load / ship / return date;
Character03 = status. Calculated_OrderVal is GBP, as confirmed by transport;
the dashboard displays it in pounds without currency conversion.
"""

from app.extensions import db


LOAD_STAGES = ("PLANNED", "SCHEDULED", "PACKED", "SHIPPED")


class TransportLoad(db.Model):
    __tablename__ = "transport_loads"

    id = db.Column(db.Integer, primary_key=True)
    source_row_id = db.Column(db.String(100), nullable=False, unique=True)
    load_id = db.Column(db.String(100), nullable=False, index=True)
    transport_code = db.Column(db.String(100), nullable=True)
    route = db.Column(db.String(255), nullable=True)
    vehicle = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(50), nullable=False, index=True)
    capacity = db.Column(db.Numeric(18, 4), nullable=True)
    remaining = db.Column(db.Numeric(18, 4), nullable=True)
    capacity_uom = db.Column(db.String(50), nullable=True)
    available_pct = db.Column(db.Numeric(18, 4), nullable=True)
    order_qty = db.Column(db.Numeric(18, 4), nullable=True)
    order_value = db.Column(db.Numeric(18, 4), nullable=True)
    load_date = db.Column(db.Date, nullable=True)
    ship_date = db.Column(db.Date, nullable=True, index=True)
    return_date = db.Column(db.Date, nullable=True)
    load_time = db.Column(db.String(30), nullable=True)
    ship_time = db.Column(db.String(30), nullable=True)
    return_time = db.Column(db.String(30), nullable=True)
    imported_at = db.Column(db.DateTime(timezone=True), nullable=False)
