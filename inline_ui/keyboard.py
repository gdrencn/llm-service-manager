"""Terminal key decoding, independent of controls, styling and application state."""

import os
import select


class Cancelled(Exception):
    pass


def read_key(fd):
    key = os.read(fd, 1)
    if not key or key == b"\x04":
        raise EOFError
    if key == b"\x03":
        raise KeyboardInterrupt
    if key == b"\x1b":
        if not select.select([fd], [], [], 0.12)[0]:
            raise Cancelled
        prefix = os.read(fd, 1)
        if prefix not in (b"[", b"O"):
            return ""
        sequence = b""
        # Consume the complete CSI/SS3 sequence: Delete/Home/End/paste markers
        # must never leak their numeric suffix into editable text.
        while len(sequence) < 64 and select.select([fd], [], [], 0.12)[0]:
            char = os.read(fd, 1)
            if not char:
                raise EOFError
            sequence += char
            if 0x40 <= char[0] <= 0x7E:
                break
        return {
            b"A": "UP",
            b"B": "DOWN",
            b"C": "RIGHT",
            b"D": "LEFT",
            b"H": "HOME",
            b"F": "END",
            b"1~": "HOME",
            b"4~": "END",
            b"7~": "HOME",
            b"8~": "END",
            b"3~": "DELETE",
        }.get(sequence, "")
    size = 1 if key[0] < 128 else 2 if key[0] < 224 else 3 if key[0] < 240 else 4
    for _ in range(size - 1):
        char = os.read(fd, 1)
        if not char:
            raise EOFError
        key += char
    return key.decode("utf-8", errors="replace")
