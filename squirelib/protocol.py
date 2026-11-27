"""Protocol layer: the ASUS v2 framing, once for all devices.

A packet is the report ID (00 for an unnumbered report) + the command, zero-padded to the route's length. The reply
echoes the command (at [0] or [1], depending on the report-ID byte), or, for a few writes, answers with other bytes
(`expect`, e.g. per-key colours c0 81 -> c0 82). `ff aa` is provisional: the receiver sometimes sends it right before
the real reply, so it only means "asleep" when no valid reply follows.

Which commands may be sent is not decided here: exchange() writes only what its `build` gives it, and squire.packet()
(the allowlist) is the only builder Squire passes."""
import sys
from dataclasses import dataclass
from typing import Callable, Optional

from .transport import DRAIN_TIMEOUT_MS, READ_TIMEOUT_MS

MAX_READS = 8
ERROR_REPLY = b"\xff\xaa"


def frame(report_id: Optional[int], command: bytes, length: int) -> bytes:
    """Report ID (00 = unnumbered) + command, zero-padded to length. Callers check the allowlist first."""
    rid = 0x00 if report_id is None else report_id
    return (bytes([rid]) + command).ljust(length, b"\0")


def find_echo(reply: bytes, query: bytes) -> Optional[int]:
    """Return index where the echoed command starts (0 or 1, depending on report-ID byte)."""
    for base in (0, 1):
        if reply[base:base + len(query)] == query:
            return base
    return None


def is_error(reply: bytes, report_id: Optional[int]) -> bool:
    for base in (0, 1):
        if reply[base:base + 2] == ERROR_REPLY:
            if base == 1 and report_id is not None and reply[0] != report_id:
                continue
            return True
    return False


@dataclass
class Exchange:
    status: str                      # ok | asleep | no-response | error
    reply: bytes = b""
    base: int = 0                    # index of the echoed command in reply
    raw: str = ""

    @property
    def payload(self) -> bytes:
        """Reply from the echoed command on (indices as in protocol.json notes, without report ID)."""
        return self.reply[self.base:]


def exchange(dev, target, command: bytes, build: Callable[[int], bytes], *, expect: Optional[bytes] = None,
             debug: bool = False, match: Optional[int] = None) -> Exchange:
    """Send one command and wait for the reply that echoes it (first `match` bytes), or for `expect` where the
    device acknowledges with other bytes. build(length) returns the packet for one of target.report_lens."""
    echo = expect or command[:match or len(command)]
    # drain stale input (bounded, short timeout)
    for _ in range(MAX_READS):
        if not dev.read(65, DRAIN_TIMEOUT_MS):
            break
    written, last_error = -1, "write failed"
    for length in target.report_lens:
        data = build(length)
        try:
            written = dev.write(data)
        except (OSError, ValueError) as error:
            written, last_error = -1, f"write failed: {error}"
        if debug:
            print(f"[{target.device}] > {data[:5].hex(' ')} … ({length} bytes) -> {written}", file=sys.stderr)
        if written is not None and written >= 0:
            break
    if written is None or written < 0:
        return Exchange("error", raw=last_error)
    saw_error, raw = False, ""
    for _ in range(MAX_READS):
        reply = bytes(dev.read(65, READ_TIMEOUT_MS))
        if not reply:
            if debug:
                print(f"[{target.device}] < (timeout)", file=sys.stderr)
            break
        if debug:
            print(f"[{target.device}] < {reply.hex(' ')}", file=sys.stderr)
        if is_error(reply, target.report_id):
            # the receiver sometimes sends ff aa right before the real reply: keep reading
            saw_error, raw = True, reply.hex(" ")
            continue
        base = find_echo(reply, echo)
        if base is None:
            continue  # other traffic (e.g. button/profile events); keep reading
        return Exchange("ok", reply, base, reply.hex(" "))
    return Exchange("asleep" if saw_error else "no-response", raw=raw)
