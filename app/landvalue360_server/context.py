"""Authenticated request context."""

from __future__ import annotations

from dataclasses import dataclass

from .enums import Permission
from .permissions import role_permissions


@dataclass(frozen=True, slots=True)
class AuthContext:
    user_id: str
    email: str
    full_name: str
    organization_id: str | None
    workspace_id: str | None
    membership_id: str | None
    role: str
    is_platform_admin: bool
    session_id: str
    edition_scope: str = "COMBINED"
    request_id: str | None = None

    @property
    def permissions(self) -> frozenset[Permission]:
        return role_permissions(self.role, platform_admin=self.is_platform_admin)

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions
