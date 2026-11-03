"""Working upload kept for one run of the app.

The Reviews page writes the uploaded file to disk so a refresh, or a trip
to Policy Management and back, can show the same queue. That copy belongs
to the running process. Starting the app again drops it, so the next run
asks for a file. The decision log is separate and is not deleted here.
"""

from __future__ import annotations

from pathlib import Path

_forgotten = False


def forget_stored_upload_once(paths: list[Path]) -> None:
    """Delete the stored upload the first time this process asks.

    Later calls in the same process do nothing, so a refresh keeps the file.
    A new process starts with ``_forgotten`` false and drops the copy again.
    """
    global _forgotten
    if _forgotten:
        return
    _forgotten = True
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue
