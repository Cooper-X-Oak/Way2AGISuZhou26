"""Immutable SHA-256 blobs. Atomic publication prevents partial reads."""
import hashlib
import os
from pathlib import Path
import re
import tempfile

from app.db import ROOT


class BlobStore:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else ROOT / "data/blobs"

    def path(self, sha):
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ValueError("invalid SHA-256")
        return self.root / sha[:2] / sha

    def put(self, content: bytes) -> str:
        sha = hashlib.sha256(content).hexdigest()
        target = self.path(sha)
        if target.exists():
            self.get(sha)
            return sha
        target.parent.mkdir(parents=True, exist_ok=True)
        # Temporary files stay within the blob store, on the same filesystem.
        fd, temporary = tempfile.mkstemp(prefix=".write-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                # Publish only if absent; replacing a file held by a reader fails
                # on Windows. Hard linking is atomic and never overwrites a blob.
                os.link(temporary, target)
            except FileExistsError:
                self.get(sha)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return sha

    def get(self, sha: str) -> bytes:
        content = self.path(sha).read_bytes()
        if hashlib.sha256(content).hexdigest() != sha:
            raise ValueError(f"blob hash mismatch: {sha}")
        return content
