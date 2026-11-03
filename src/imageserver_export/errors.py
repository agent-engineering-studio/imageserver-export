"""The one exception this package raises for answers it cannot use."""

from __future__ import annotations


class ImageServerError(Exception):
    """The ImageServer answered, but not with what was asked for."""
