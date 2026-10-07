"""Index store: FAISS (dense) + BM25 (lexical) over one identical chunk list, guarded by a manifest.

Files in `indexes/<provider>-<model>/`:
  manifest.json   embedder identity, dim, normalisation, counts, source hashes, chunker version, content hash
  chunks.jsonl    the chunk list (order = BM25 doc order = owner of FAISS rows)
  index.faiss     IndexFlatIP over L2-normalised vectors (cosine)
  vectors_owner.json   FAISS row -> chunk index (FAQ chunks own two rows: Q+A and question-only)
  bm25.json       BM25 term frequencies
  conflicts.json  conflict groups found by the audit detectors at ingest time (context assembly uses them)
  missing_sections.json  sections listed in a TOC whose body is absent (answer notes use them)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import faiss
import numpy as np
from pydantic import BaseModel

from app.ingest.models import Chunk
from app.providers.base import Vectors
from app.retrieval.bm25 import BM25
from app.settings import Settings
from app.text.tokenize import tokenize

#: Known output dimensions (used to catch a wrong index before the first query).
KNOWN_DIMS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class IndexManifest(BaseModel):
    """Identity of an index. Anything that changes vectors must change this."""

    embedder_provider: str
    embedder_model: str
    dim: int
    normalized: bool
    n_chunks: int
    n_vectors: int
    source_pdf_sha256s: dict[str, str]
    chunker_version: str
    built_at: str
    chunks_sha256: str
    content_hash: str = ""

    def compute_content_hash(self) -> str:
        """Hash of every field except `built_at`/`content_hash` (identical across re-ingests)."""
        payload = self.model_dump(exclude={"built_at", "content_hash"})
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


class IndexMismatchError(RuntimeError):
    """The on-disk index is incompatible with the configured embedder (fail fast at startup)."""


class IndexNotFoundError(FileNotFoundError):
    """No index directory for the configured embedder."""


def lexical_text(chunk: Chunk) -> str:
    """Text indexed by BM25: header + body (+ FAQ question)."""
    return f"{chunk.header} {chunk.text}"


def validate_manifest(manifest: IndexManifest, settings: Settings) -> None:
    """Raise IndexMismatchError unless the manifest matches the configured embedder exactly."""
    problems: list[str] = []
    if manifest.embedder_provider != settings.effective_embed_provider:
        problems.append(
            f"provider {manifest.embedder_provider!r} != configured {settings.effective_embed_provider!r}"
        )
    if manifest.embedder_model != settings.embed_model:
        problems.append(f"model {manifest.embedder_model!r} != configured {settings.embed_model!r}")
    expected_dim = KNOWN_DIMS.get(settings.embed_model)
    if expected_dim is not None and manifest.dim != expected_dim:
        problems.append(f"dim {manifest.dim} != expected {expected_dim} for {settings.embed_model}")
    if not manifest.normalized:
        problems.append("vectors are not L2-normalised")
    if manifest.content_hash and manifest.content_hash != manifest.compute_content_hash():
        problems.append("manifest content_hash does not match its fields (edited or corrupted)")
    if problems:
        raise IndexMismatchError(
            "Index/embedder mismatch: "
            + "; ".join(problems)
            + ". Rebuild with `python -m app.ingest` for the configured OPENAI_EMBED_MODEL."
        )


@dataclass
class IndexStore:
    """Loaded, validated index."""

    directory: Path
    manifest: IndexManifest
    chunks: list[Chunk]
    faiss_index: Any
    owners: np.ndarray
    bm25: BM25
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    missing_sections: list[dict[str, str]] = field(default_factory=list)

    @property
    def dim(self) -> int:
        """Vector dimension."""
        return int(self.faiss_index.d)

    @property
    def manifest_hash(self) -> str:
        """Hash used to key retrieval caches."""
        return self.manifest.content_hash

    @classmethod
    def load(cls, directory: Path, settings: Settings) -> IndexStore:
        """Load and validate; raises IndexNotFoundError / IndexMismatchError with actionable messages."""
        manifest_path = directory / "manifest.json"
        if not manifest_path.exists():
            raise IndexNotFoundError(
                f"No index at {directory}. Build it with `python -m app.ingest` (needs OPENAI_API_KEY)."
            )
        manifest = IndexManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        validate_manifest(manifest, settings)
        chunks_bytes = (directory / "chunks.jsonl").read_bytes()
        if hashlib.sha256(chunks_bytes).hexdigest() != manifest.chunks_sha256:
            raise IndexMismatchError(
                "chunks.jsonl does not match the manifest (partial copy?). Rebuild the index."
            )
        chunks = [
            Chunk.model_validate_json(line)
            for line in chunks_bytes.decode("utf-8").splitlines()
            if line.strip()
        ]
        index = faiss.read_index(str(directory / "index.faiss"))
        owners = np.asarray(
            json.loads((directory / "vectors_owner.json").read_text(encoding="utf-8")), dtype=np.int64
        )
        bm25 = BM25.from_dict(json.loads((directory / "bm25.json").read_text(encoding="utf-8")))
        conflicts_path = directory / "conflicts.json"
        conflicts = json.loads(conflicts_path.read_text(encoding="utf-8")) if conflicts_path.exists() else []
        missing_path = directory / "missing_sections.json"
        missing = json.loads(missing_path.read_text(encoding="utf-8")) if missing_path.exists() else []
        store = cls(directory, manifest, chunks, index, owners, bm25, conflicts, missing)
        store.check_consistency()
        return store

    def check_consistency(self) -> None:
        """FAISS rows, owners, BM25 docs and chunks must line up exactly."""
        if self.dim != self.manifest.dim:
            raise IndexMismatchError(f"FAISS dim {self.dim} != manifest dim {self.manifest.dim}")
        if self.faiss_index.ntotal != self.manifest.n_vectors or len(self.owners) != self.manifest.n_vectors:
            raise IndexMismatchError("vector count does not match manifest")
        if len(self.chunks) != self.manifest.n_chunks or len(self.bm25) != len(self.chunks):
            raise IndexMismatchError("chunk count / BM25 doc count does not match manifest")
        if len(self.owners) and (self.owners.min() < 0 or self.owners.max() >= len(self.chunks)):
            raise IndexMismatchError("vector owner points outside the chunk list")

    def check_query_vector(self, vector: Vectors) -> None:
        """Runtime guard: a query vector from a different embedder must never be searched."""
        if vector.ndim != 2 or vector.shape[1] != self.dim:
            raise IndexMismatchError(f"query vector dim {vector.shape[-1]} != index dim {self.dim}")
        norms = np.linalg.norm(vector, axis=1)
        if not np.allclose(norms, 1.0, atol=1e-3):
            raise IndexMismatchError("query vector is not L2-normalised")

    def dense_search(self, query_vector: Vectors, k: int) -> list[tuple[int, float]]:
        """Top-k chunks by cosine; a chunk owning several rows keeps its best score."""
        self.check_query_vector(query_vector)
        n_rows = min(self.faiss_index.ntotal, max(k * 3, k))
        scores, rows = self.faiss_index.search(query_vector.astype(np.float32), n_rows)
        best: dict[int, float] = {}
        for row, score in zip(rows[0], scores[0], strict=True):
            if row < 0:
                continue
            owner = int(self.owners[row])
            best[owner] = max(best.get(owner, -1.0), float(score))
        ranked = sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked[:k]

    def bm25_search(self, query_text: str, k: int) -> list[tuple[int, float]]:
        """Top-k chunks by BM25 over the lexical text."""
        return self.bm25.top_k(tokenize(query_text), k)

    def chunk_by_id(self, chunk_id: str) -> Chunk | None:
        """Lookup by chunk id."""
        return next((c for c in self.chunks if c.chunk_id == chunk_id), None)


def build_faiss(vectors: Vectors) -> Any:
    """Exact inner-product index (vectors must already be normalised)."""
    index = faiss.IndexFlatIP(int(vectors.shape[1]))
    index.add(vectors.astype(np.float32))
    return index


def write_store(
    directory: Path,
    chunks: list[Chunk],
    vectors: Vectors,
    owners: list[int],
    manifest_fields: dict[str, Any],
    conflicts: list[dict[str, Any]] | None = None,
    missing_sections: list[dict[str, str]] | None = None,
) -> IndexManifest:
    """Write every index file atomically-ish (manifest last) and return the manifest."""
    directory.mkdir(parents=True, exist_ok=True)
    chunks_bytes = "".join(
        json.dumps(c.model_dump(), ensure_ascii=False, sort_keys=True) + "\n" for c in chunks
    ).encode("utf-8")
    (directory / "chunks.jsonl").write_bytes(chunks_bytes)
    faiss.write_index(build_faiss(vectors), str(directory / "index.faiss"))
    (directory / "vectors_owner.json").write_text(json.dumps(owners), encoding="utf-8")
    bm25 = BM25.build([tokenize(lexical_text(c)) for c in chunks])
    (directory / "missing_sections.json").write_text(
        json.dumps(missing_sections or [], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (directory / "conflicts.json").write_text(
        json.dumps(conflicts or [], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (directory / "bm25.json").write_text(
        json.dumps(bm25.to_dict(), ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    manifest = IndexManifest(
        dim=int(vectors.shape[1]),
        normalized=bool(np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-3)),
        n_chunks=len(chunks),
        n_vectors=int(vectors.shape[0]),
        chunks_sha256=hashlib.sha256(chunks_bytes).hexdigest(),
        **manifest_fields,
    )
    manifest.content_hash = manifest.compute_content_hash()
    (directory / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest
