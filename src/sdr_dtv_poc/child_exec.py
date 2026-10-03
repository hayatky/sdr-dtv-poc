# SPDX-License-Identifier: GPL-3.0-or-later
"""Linux child launcher: kill external media tools if their API parent dies."""

import ctypes
import os
import signal
import sys


def main() -> None:
    parent = int(sys.argv[1])
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
        raise SystemExit(1)
    os.execvp(sys.argv[2], sys.argv[2:])


if __name__ == "__main__":
    main()
