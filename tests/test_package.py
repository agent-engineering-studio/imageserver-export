from __future__ import annotations

import imageserver_export


def test_version_is_exposed() -> None:
    assert imageserver_export.__version__
