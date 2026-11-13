"""Import every model so Base.metadata is complete (used by Alembic)."""

from app.db.models.admin import AdminSession, AdminUser
from app.db.models.conversation import Session, Turn
from app.db.models.explainer import Explainer, ExplainerSegment
from app.db.models.knowledge import (
    Chunk,
    Faculty,
    Faq,
    Lab,
    Media,
    Pitch,
    Place,
    Program,
    Source,
)
from app.db.models.telemetry import Event, PoseSample, ReturnTrial, RobotRun

__all__ = [
    "AdminSession",
    "AdminUser",
    "Chunk",
    "Event",
    "Explainer",
    "ExplainerSegment",
    "Faculty",
    "Faq",
    "Lab",
    "Media",
    "Pitch",
    "Place",
    "PoseSample",
    "Program",
    "ReturnTrial",
    "RobotRun",
    "Session",
    "Source",
    "Turn",
]
