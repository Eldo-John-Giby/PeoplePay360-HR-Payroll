"""Service layer for the Employee module. OWNER: Ameen.

Business rules that belong here (from 01_DB_SCHEMA_ELDO.md):
- Running-contract exclusivity (expire-then-activate in ONE service call —
  expose as a single "Activate Contract" action, not a raw status PATCH).
- Referential existence checks for every FK in request bodies.
- Pagination clamping (page >= 1, 1 <= page_size <= 200).
"""

# TODO(Ameen): implement