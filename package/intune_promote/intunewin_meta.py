"""Parse .intunewin (zip) Detection.xml + encrypted content bytes."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any, Dict, Tuple
from xml.etree import ElementTree as ET


def _text(parent: ET.Element, tag: str) -> str:
    node = parent.find(tag)
    if node is None or node.text is None:
        raise ValueError(f"Detection.xml missing {tag}")
    return node.text


def _encryption_fields(enc: ET.Element) -> Dict[str, str]:
    return {
        "encryption_key": _text(enc, "EncryptionKey"),
        "mac_key": _text(enc, "MacKey"),
        "initialization_vector": _text(enc, "InitializationVector"),
        "mac": _text(enc, "Mac"),
        "profile_identifier": _text(enc, "ProfileIdentifier"),
        "file_digest": _text(enc, "FileDigest"),
        "file_digest_algorithm": _text(enc, "FileDigestAlgorithm"),
    }


def _find_entry(names: list[str], predicate) -> str:
    for name in names:
        if predicate(name):
            return name
    raise ValueError("intunewin zip missing required entry")


def _read_detection_root(zf: zipfile.ZipFile) -> Tuple[ET.Element, str]:
    detection_name = _find_entry(
        zf.namelist(), lambda n: n.lower().endswith("detection.xml")
    )
    return ET.fromstring(zf.read(detection_name)), detection_name


def _read_encrypted_content(zf: zipfile.ZipFile, file_name: str) -> bytes:
    needle = file_name.lower()

    def match(name: str) -> bool:
        lower = name.lower()
        return lower.endswith(needle) or "/contents/" in lower

    content_name = _find_entry(zf.namelist(), match)
    return zf.read(content_name)


def parse_intunewin_metadata(intunewin_path: Path) -> Dict[str, Any]:
    """Extract encryption metadata and encrypted payload from an .intunewin."""
    with zipfile.ZipFile(intunewin_path, "r") as zf:
        root, _ = _read_detection_root(zf)
        enc = root.find("EncryptionInfo")
        if enc is None:
            raise ValueError("Detection.xml missing EncryptionInfo")
        file_name = _text(root, "FileName")
        encrypted = _read_encrypted_content(zf, file_name)
        meta = _encryption_fields(enc)
        meta.update(
            {
                "file_name": file_name,
                "unencrypted_content_size": int(_text(root, "UnencryptedContentSize")),
                "size_encrypted": len(encrypted),
                "encrypted_content": encrypted,
            }
        )
        return meta
