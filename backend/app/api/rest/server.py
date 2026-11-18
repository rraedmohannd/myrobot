"""GET /api/admin/server: the admin "Server" card (sprint 6 P1)."""

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.services.server_status import server_status

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/server")
async def server() -> dict[str, Any]:
    """Production mode, paid AI calls today against DAILY_PAID_CALL_LIMIT, the last backup,
    disk usage of the data volume and the TLS certificate expiry (null when unknown)."""
    return await server_status()
