"""Política de seguridad (deny-by-default)."""

from satellite.security.base import SecurityPolicy
from satellite.security.policy import DefaultSecurityPolicy

__all__ = ["SecurityPolicy", "DefaultSecurityPolicy"]
