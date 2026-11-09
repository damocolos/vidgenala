"""Pure HTTP Range header parsing (RFC 7233, single range)."""
import re
from typing import Optional, Tuple

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class RangeNotSatisfiable(ValueError):
    pass


def parse_range(header: Optional[str], size: int) -> Optional[Tuple[int, int]]:
    """Return (start, end) inclusive, or None to serve the whole file.

    - ``bytes=a-b``  end is clamped to size-1
    - ``bytes=a-``   to end of file
    - ``bytes=-n``   last n bytes
    - malformed / multi-range / non-bytes units -> None (full response, as allowed by the RFC)
    Raises RangeNotSatisfiable when the range lies entirely outside the file.
    """
    if not header:
        return None
    m = _RANGE_RE.match(header.strip())
    if not m:
        return None
    a, b = m.group(1), m.group(2)
    if a == "" and b == "":
        return None
    if size <= 0:
        raise RangeNotSatisfiable("empty file")
    if a == "":  # suffix range
        n = int(b)
        if n == 0:
            raise RangeNotSatisfiable("zero-length suffix")
        return max(0, size - n), size - 1
    start = int(a)
    end = int(b) if b else size - 1
    if start >= size or (b and int(b) < start):
        raise RangeNotSatisfiable("range outside file")
    return start, min(end, size - 1)
