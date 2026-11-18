"""Import every model so Base.metadata is complete (used by Alembic)."""

from app.db.models.admin import AdminSession, AdminUser
from app.db.models.bringup import BringupStep, CalibrationRecord
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
from app.db.models.usage import PaidCallDay

__all__ = [
    "AdminSession",
    "AdminUser",
    "BringupStep",
    "CalibrationRecord",
    "Chunk",
    "Event",
    "Explainer",
    "ExplainerSegment",
    "Faculty",
    "Faq",
    "Lab",
    "Media",
    "PaidCallDay",
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
