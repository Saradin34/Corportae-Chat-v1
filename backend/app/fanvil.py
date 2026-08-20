"""Backward compatibility wrapper for old Fanvil-specific imports.

Direct click-to-call is now implemented in app.phone_control and can be used
with any IP phone that supports an HTTP dial/action URL, not only Fanvil.
"""
from __future__ import annotations

from .phone_control import fanvil_dial, ip_phone_dial, phone_ip_for_extension

__all__ = ["fanvil_dial", "ip_phone_dial", "phone_ip_for_extension"]
