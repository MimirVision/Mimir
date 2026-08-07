"""Send files to the Windows Recycle Bin.

Mimir's storage actions were built on a promise -- "it never permanently
deletes files" -- and trash meant moving clips into a folder that nothing ever
emptied. That is honest but it is not what people mean by trash: the clips
stopped being on the USB stick and started being on the system drive forever
instead.

Deleting properly needs somewhere to put a mistake. After an import that moves
footage off the stick, Mimir's copy is the *only* copy, so a mis-click that
unlinks a file is unrecoverable -- possibly destroying the footage of the
incident the user bought a dashcam for. The Recycle Bin is the recovery path
Windows users already understand, and it costs nothing to use.

Implemented against SHFileOperationW through ctypes rather than by adding a
dependency such as Send2Trash. This is one documented call, it has to be
bundled into a PyInstaller sidecar, and it has to survive a release security
audit; a pure-stdlib implementation is the cheaper thing to own.

Note that this frees no space until the user empties the bin. That is the
trade, and it is the caller's job to say so rather than imply otherwise.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

# SHFileOperationW
FO_DELETE = 0x0003
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040          # the flag that means "Recycle Bin", not "unlink"
FOF_NOERRORUI = 0x0400
FOF_NOCONFIRMMKDIR = 0x0200


class RecycleBinUnavailable(RuntimeError):
    """The Recycle Bin cannot be used here, and the caller must not pretend otherwise."""


@dataclass
class RecycleResult:
    path: str
    ok: bool
    reason: str = ""


def _strip_extended_prefix(path: str) -> str:
    r"""Remove the \\?\ prefix that Mimir's own session paths carry.

    The scanner writes extended-length paths, and the shell API rejects them
    outright -- SHFileOperationW is documented as not supporting \\?\. Passing
    one through returns a generic failure code that looks like a permissions
    problem, so this is worth doing explicitly rather than discovering twice.
    """

    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def available() -> bool:
    return sys.platform == "win32"


def send_to_recycle_bin(paths: list[Path]) -> list[RecycleResult]:
    """Send each path to the Recycle Bin. Missing paths count as already gone.

    One call per path rather than one batched call for all of them: the batch
    form reports a single result for the whole operation, so a partial failure
    is indistinguishable from a total one and the report would have to guess
    which files actually went.
    """

    if not available():
        raise RecycleBinUnavailable(
            f"The Recycle Bin is only available on Windows, not {sys.platform}."
        )

    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            # FILEOP_FLAGS is a WORD. Declaring it as UINT silently shifts
            # every field after it on some builds.
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    shell = ctypes.windll.shell32
    shell.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    shell.SHFileOperationW.restype = ctypes.c_int

    results: list[RecycleResult] = []

    for path in paths:
        original = str(path)
        if not path.exists():
            results.append(RecycleResult(original, True, "already gone"))
            continue

        shell_path = _strip_extended_prefix(os.path.abspath(_strip_extended_prefix(original)))

        operation = SHFILEOPSTRUCTW()
        operation.hwnd = None
        operation.wFunc = FO_DELETE
        # pFrom is a list of strings terminated by an extra NUL. ctypes adds
        # one for the string itself, so the explicit "\0" here is the list
        # terminator. Without it the call reads past the buffer.
        operation.pFrom = shell_path + "\0"
        operation.pTo = None
        operation.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI | FOF_NOCONFIRMMKDIR
        operation.fAnyOperationsAborted = False
        operation.hNameMappings = None
        operation.lpszProgressTitle = None

        code = shell.SHFileOperationW(ctypes.byref(operation))

        if code != 0:
            results.append(
                RecycleResult(original, False, f"the Recycle Bin refused this file (code {code})")
            )
        elif operation.fAnyOperationsAborted:
            results.append(RecycleResult(original, False, "the operation was aborted"))
        elif path.exists():
            # Belt and braces: a success code with the file still present means
            # something took the request and did nothing with it.
            results.append(RecycleResult(original, False, "reported success but the file is still there"))
        else:
            results.append(RecycleResult(original, True))

    return results
