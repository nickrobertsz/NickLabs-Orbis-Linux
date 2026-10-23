"""NickLabs PS4 Orbis Linux platform layer.

The ``orbis`` package implements the mode engine, hardware inventory,
capability matrix, boot-partition adapter, diagnostics and the local
control-plane API used by the ``orbis`` CLI, ``orbisd`` and ``orbis-web``.

Only the Python standard library is used so the platform layer adds no
dependencies to the base image beyond ``python``.
"""

__version__ = "0.1.0"

#: Version of the local JSON API spoken over the orbisd socket and exposed
#: by orbis-web under /api/v1. Bump on any incompatible change.
API_VERSION = 1

MODES = ("pc", "gaming", "ai")
