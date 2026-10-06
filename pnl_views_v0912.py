"""Compatibility shim for the confirmed P&L view.

v0.9.321 moves the real implementation to pnl_views_v09321 so stale copies of
this historical module name cannot keep the old confirmed-profit table alive.
"""
from pnl_views_v09321 import *  # noqa: F401,F403
