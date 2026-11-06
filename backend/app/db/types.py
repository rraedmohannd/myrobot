"""Minimal pgvector column type.

Only the DDL is needed from SQLAlchemy: embeddings are written and searched with explicit
SQL casts (CAST(:e AS vector)), so no numpy/pgvector client library is required.
"""

from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType):
    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **_: object) -> str:
        return f"vector({self.dim})"


def to_pgvector(values: list[float]) -> str:
    """Python list -> pgvector text literal, e.g. '[0.1,0.2]'."""
    return "[" + ",".join(f"{v:.7g}" for v in values) + "]"


def alembic_compare_type(context, inspected_column, metadata_column, inspected_type, metadata_type):  # noqa: ANN001, ANN201
    """SQLAlchemy cannot reflect pgvector columns (it sees NullType), so never report them
    as a type change; everything else uses Alembic's default comparison (None)."""
    if isinstance(metadata_type, Vector):
        return False
    return None
