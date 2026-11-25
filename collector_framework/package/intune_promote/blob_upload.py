"""Azure blob block upload for Intune Win32 encrypted content (stdlib)."""

from __future__ import annotations

import base64
import urllib.request
from typing import Callable


def upload_blocks(
    azure_storage_uri: str,
    content: bytes,
    *,
    chunk_size: int = 6 * 1024 * 1024,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> None:
    """Upload content as a single Azure block blob via block list."""
    if not content:
        raise ValueError("encrypted content is empty")
    block_ids = []
    offset = 0
    index = 0
    while offset < len(content):
        chunk = content[offset : offset + chunk_size]
        block_id = base64.b64encode(f"{index:04d}".encode("ascii")).decode("ascii")
        block_ids.append(block_id)
        url = f"{azure_storage_uri}&comp=block&blockid={block_id}"
        req = urllib.request.Request(
            url,
            data=chunk,
            method="PUT",
            headers={"x-ms-blob-type": "BlockBlob", "Content-Length": str(len(chunk))},
        )
        with opener(req):
            pass
        offset += len(chunk)
        index += 1
    xml = '<?xml version="1.0" encoding="utf-8"?><BlockList>'
    xml += "".join(f"<Latest>{bid}</Latest>" for bid in block_ids)
    xml += "</BlockList>"
    list_url = f"{azure_storage_uri}&comp=blocklist"
    list_req = urllib.request.Request(
        list_url,
        data=xml.encode("utf-8"),
        method="PUT",
        headers={"Content-Type": "text/plain; charset=UTF-8"},
    )
    with opener(list_req):
        pass
