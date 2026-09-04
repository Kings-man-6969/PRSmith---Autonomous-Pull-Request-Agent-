"""Production-grade semantic retrieval over code embeddings using vector similarity."""

import hashlib
import math
import uuid
from typing import Any, Dict, List, Optional
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
try:
    import openai
except ImportError:
    openai = None

from backend.config import settings
from backend.database.models import CodeEmbedding
from backend.observability.logging import get_logger

logger = get_logger(__name__)

VECTOR_DIM = 768


def is_valid_credential(val: Optional[str]) -> bool:
    """Return True only if credential is real and not an unconfigured placeholder."""
    if not val or not val.strip():
        return False
    v = val.strip().lower()
    if any(placeholder in v for placeholder in ["your-", "your_", "change-me", "placeholder", "sk-your"]):
        return False
    return True


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Compute cosine similarity between two vector lists."""
    if not v1 or not v2:
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return dot / (norm1 * norm2)


def generate_feature_vector(text: str, dim: int = VECTOR_DIM) -> List[float]:
    """
    Deterministic feature hashing vector representation.
    Used for offline/local semantic search or fallback when external embedding APIs are unavailable.
    """
    vec = [0.0] * dim
    tokens = [t.strip().lower() for t in text.split() if t.strip()]
    if not tokens:
        return vec

    # N-gram token hashing
    for i, tok in enumerate(tokens):
        h1 = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16) % dim
        vec[h1] += 1.0

        if i + 1 < len(tokens):
            bigram = f"{tok}_{tokens[i+1]}"
            h2 = int(hashlib.sha256(bigram.encode("utf-8")).hexdigest(), 16) % dim
            vec[h2] += 1.5

    # L2 normalize
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


class SemanticRetriever:
    """Indexes code chunks and performs production-grade semantic search."""

    def __init__(self, api_key: Optional[str] = None):
        self.openai_key = api_key if is_valid_credential(api_key) else (
            getattr(settings, "OPENAI_API_KEY", "") if is_valid_credential(getattr(settings, "OPENAI_API_KEY", "")) else ""
        )
        self.gemini_key = getattr(settings, "GEMINI_API_KEY", "") if is_valid_credential(getattr(settings, "GEMINI_API_KEY", "")) else ""
        self.openai_client = openai.AsyncOpenAI(api_key=self.openai_key) if self.openai_key else None

    async def get_embedding(self, text: str) -> List[float]:
        """Generate high-fidelity vector embedding using OpenAI, Gemini, or feature hashing."""
        truncated_text = text[:8000].strip()
        if not truncated_text:
            return [0.0] * VECTOR_DIM

        # 1. Try OpenAI if valid key is present
        if self.openai_client and self.openai_key:
            try:
                res = await self.openai_client.embeddings.create(
                    model=settings.OPENAI_EMBEDDING_MODEL or "text-embedding-3-small",
                    input=truncated_text,
                )
                return res.data[0].embedding
            except Exception as e:
                logger.warning("OpenAI embedding failed, falling back", error=str(e))

        # 2. Try Gemini embedding if valid key is present
        if self.gemini_key:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={self.gemini_key}"
                payload = {
                    "model": "models/text-embedding-004",
                    "content": {"parts": [{"text": truncated_text}]},
                }
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(url, json=payload)
                    if resp.status_code == 200:
                        values = resp.json().get("embedding", {}).get("values", [])
                        if values:
                            return values
            except Exception as e:
                logger.warning("Gemini embedding failed, falling back", error=str(e))

        # 3. Deterministic feature hashing fallback
        return generate_feature_vector(truncated_text, dim=VECTOR_DIM)

    async def index_chunks(
        self,
        session: AsyncSession,
        repository_id: str,
        commit_sha: str,
        chunks: List[Dict[str, Any]],
    ) -> None:
        """Store chunk embeddings in PostgreSQL."""
        if not chunks:
            return

        embeddings_to_add = []
        for ch in chunks:
            text = ch.get("chunk_text", "")
            if not text.strip():
                continue
            vec = await self.get_embedding(text)
            emb = CodeEmbedding(
                id=str(uuid.uuid4()),
                repository_id=repository_id,
                commit_sha=commit_sha,
                file_path=ch.get("file_path", ""),
                symbol_name=ch.get("symbol_name"),
                language=ch.get("language", "python"),
                chunk_text=text,
                embedding_json=vec,
            )
            embeddings_to_add.append(emb)

        session.add_all(embeddings_to_add)
        await session.flush()
        logger.info(
            "Indexed code embeddings",
            repository_id=repository_id,
            commit_sha=commit_sha,
            count=len(embeddings_to_add),
        )

    async def search(
        self,
        session: AsyncSession,
        repository_id: str,
        commit_sha: str,
        query: str,
        top_k: int = 10,
    ) -> List[Dict[str, Any]]:
        """Find semantically similar code chunks using cosine similarity."""
        if not query.strip():
            return []

        query_vec = await self.get_embedding(query)

        stmt = (
            select(CodeEmbedding)
            .where(CodeEmbedding.repository_id == repository_id)
            .where(CodeEmbedding.commit_sha == commit_sha)
        )
        res = await session.execute(stmt)
        candidates = res.scalars().all()

        scored = []
        for item in candidates:
            score = cosine_similarity(query_vec, item.embedding_json or [])
            scored.append({
                "file_path": item.file_path,
                "symbol_name": item.symbol_name,
                "chunk_text": item.chunk_text,
                "score": score,
                "type": "semantic",
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]
