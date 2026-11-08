"""HTTP Range streaming helper for HTML5 audio and video playback."""
import os
import re
from pathlib import Path
from typing import Optional
from fastapi import Request, HTTPException
from fastapi.responses import Response, StreamingResponse


def range_stream_response(
    file_path: Path,
    request: Request,
    content_type: str = "video/mp4",
) -> Response:
    """Streams a file with HTTP Range (206 Partial Content) support."""
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Media file not found.")

    file_size = file_path.stat().st_size
    range_header = request.headers.get("range")

    if not range_header:
        # Full content response
        def iter_full():
            with open(file_path, "rb") as f:
                while chunk := f.read(64 * 1024):
                    yield chunk

        headers = {
            "Content-Length": str(file_size),
            "Accept-Ranges": "bytes",
            "Content-Type": content_type,
        }
        return StreamingResponse(iter_full(), headers=headers, media_type=content_type)

    # Parse Range: bytes=start-end
    match = re.match(r"^bytes=(\d+)-(\d*)$", range_header.strip())
    if not match:
        raise HTTPException(status_code=416, detail="Invalid Range header format.")

    start = int(match.group(1))
    end_str = match.group(2)
    end = int(end_str) if end_str else file_size - 1

    if start >= file_size or end >= file_size or start > end:
        raise HTTPException(
            status_code=416,
            detail="Range not satisfiable.",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    content_length = end - start + 1

    def iter_range():
        with open(file_path, "rb") as f:
            f.seek(start)
            bytes_left = content_length
            chunk_size = 64 * 1024
            while bytes_left > 0:
                to_read = min(chunk_size, bytes_left)
                data = f.read(to_read)
                if not data:
                    break
                bytes_left -= len(data)
                yield data

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": content_type,
    }
    return StreamingResponse(iter_range(), status_code=206, headers=headers, media_type=content_type)
