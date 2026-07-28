"""Implicit-feedback ALS matrix factorisation, in numpy.

Why not the `implicit` library: it pulls a compiled BLAS-heavy dependency that
inflates the Docker image past what Render's free tier builds comfortably, and
at our scale (<10k users, <10k books) a plain numpy solver is fast enough —
a full fit is seconds, not minutes.

Implements Hu, Koren & Volinsky (2008): confidence-weighted ALS where a missing
entry means "no evidence", not "disliked". That distinction matters for a
reading app, where users interact with a tiny fraction of the catalogue.
"""

import json
import os
import time
from collections.abc import Sequence
from typing import Optional

import numpy as np

from app.core.logging_config import get_logger

logger = get_logger(__name__)


class ALSModel:
    """Confidence-weighted ALS over a user x item interaction matrix."""

    def __init__(
        self,
        factors: int = 32,        # small: we have far more books than signal
        iterations: int = 15,
        regularization: float = 0.05,
        alpha: float = 20.0,      # confidence scaling: c = 1 + alpha * r
        seed: int = 42,
    ):
        self.factors = factors
        self.iterations = iterations
        self.regularization = regularization
        self.alpha = alpha
        self.seed = seed

        self.user_factors: np.ndarray | None = None
        self.item_factors: np.ndarray | None = None
        self.user_index: dict[str, int] = {}
        self.item_index: dict[str, int] = {}
        self._item_ids: list[str] = []
        self.trained_at: float | None = None
        self.metrics: dict[str, float] = {}

    # -- Training ---------------------------------------------------------

    @staticmethod
    def _solve(
        fixed: np.ndarray,
        preferences: np.ndarray,
        confidences: np.ndarray,
        regularization: float,
        gramian: np.ndarray,
    ) -> np.ndarray:
        """Closed-form ALS update for one row.

        Uses the standard trick: precompute Yᵀ Y once per iteration, then per
        user only add the (sparse) Yᵀ(C-I)Y correction. Without it this is
        O(items) dense work per user and the fit becomes unusable.
        """
        if fixed.shape[0] == 0:
            return np.zeros(gramian.shape[0], dtype=np.float32)

        cu_minus_i = confidences - 1.0
        # Yᵀ(Cu - I)Y
        correction = fixed.T @ (fixed * cu_minus_i[:, None])
        a = gramian + correction + regularization * np.eye(
            gramian.shape[0], dtype=np.float32
        )
        b = fixed.T @ (confidences * preferences)
        try:
            return np.linalg.solve(a, b).astype(np.float32)
        except np.linalg.LinAlgError:
            # Singular system — fall back to the pseudo-inverse rather than
            # letting one degenerate row kill the whole fit.
            return (np.linalg.pinv(a) @ b).astype(np.float32)

    def fit(
        self, interactions: Sequence[tuple[str, str, float]]
    ) -> dict[str, float]:
        """Train from ``(user_id, book_id, strength)`` triples.

        ``strength`` should be positive implicit feedback (finished > rated >
        added). Negative signals are dropped — ALS models confidence in a
        positive, and a negative strength would invert its meaning.
        """
        started = time.time()
        positives = [(u, i, s) for u, i, s in interactions if s > 0]

        if len(positives) < 20:
            # Below this, factorisation memorises noise. The caller should
            # stay on pure content-based recommendations.
            logger.warning(
                "ALS needs >=20 positive interactions, got %d — not training",
                len(positives),
            )
            self.metrics = {"status": 0.0, "interactions": float(len(positives))}
            return self.metrics

        users = sorted({u for u, _, _ in positives})
        items = sorted({i for _, i, _ in positives})
        self.user_index = {u: n for n, u in enumerate(users)}
        self.item_index = {i: n for n, i in enumerate(items)}
        self._item_ids = items

        n_users, n_items = len(users), len(items)

        # Dense is fine at this scale: 5k x 5k float32 is 100MB, and we cap
        # well below that. Switch to scipy.sparse if the catalogue grows.
        matrix = np.zeros((n_users, n_items), dtype=np.float32)
        for user, item, strength in positives:
            matrix[self.user_index[user], self.item_index[item]] = max(
                matrix[self.user_index[user], self.item_index[item]], strength
            )

        rng = np.random.default_rng(self.seed)
        self.user_factors = rng.normal(0, 0.01, (n_users, self.factors)).astype(np.float32)
        self.item_factors = rng.normal(0, 0.01, (n_items, self.factors)).astype(np.float32)

        preference = (matrix > 0).astype(np.float32)
        confidence = 1.0 + self.alpha * matrix

        for iteration in range(self.iterations):
            item_gramian = self.item_factors.T @ self.item_factors
            for u in range(n_users):
                nz = np.nonzero(matrix[u])[0]
                if nz.size == 0:
                    continue
                self.user_factors[u] = self._solve(
                    self.item_factors[nz], preference[u, nz], confidence[u, nz],
                    self.regularization, item_gramian,
                )

            user_gramian = self.user_factors.T @ self.user_factors
            for i in range(n_items):
                nz = np.nonzero(matrix[:, i])[0]
                if nz.size == 0:
                    continue
                self.item_factors[i] = self._solve(
                    self.user_factors[nz], preference[nz, i], confidence[nz, i],
                    self.regularization, user_gramian,
                )

            if iteration == self.iterations - 1:
                loss = self._loss(matrix, preference, confidence)
                logger.debug("ALS iter %d loss=%.4f", iteration, loss)

        self.trained_at = time.time()
        self.metrics = {
            "status": 1.0,
            "users": float(n_users),
            "items": float(n_items),
            "interactions": float(len(positives)),
            "density": round(len(positives) / max(1, n_users * n_items), 6),
            "train_seconds": round(self.trained_at - started, 2),
            "loss": round(self._loss(matrix, preference, confidence), 4),
        }
        logger.info("ALS trained: %s", self.metrics)
        return self.metrics

    def _loss(
        self, matrix: np.ndarray, preference: np.ndarray, confidence: np.ndarray
    ) -> float:
        predicted = self.user_factors @ self.item_factors.T
        error = confidence * (preference - predicted) ** 2
        reg = self.regularization * (
            float(np.sum(self.user_factors**2)) + float(np.sum(self.item_factors**2))
        )
        return float(np.sum(error) + reg) / max(1, matrix.size)

    # -- Inference --------------------------------------------------------

    @property
    def is_trained(self) -> bool:
        return self.user_factors is not None and self.item_factors is not None

    def recommend(
        self,
        user_id: str,
        limit: int = 50,
        exclude: set | None = None,
    ) -> list[tuple[str, float]]:
        """Top-N ``(book_id, score)`` for a user, scores normalised to 0-1."""
        if not self.is_trained:
            return []
        idx = self.user_index.get(str(user_id))
        if idx is None:
            return []  # unseen user — caller falls back to content-based

        scores = self.item_factors @ self.user_factors[idx]
        exclude = exclude or set()

        lo, hi = float(scores.min()), float(scores.max())
        span = hi - lo

        ranked: list[tuple[str, float]] = []
        for item_idx in np.argsort(-scores):
            book_id = self._item_ids[item_idx]
            if book_id in exclude:
                continue
            raw = float(scores[item_idx])
            normalized = (raw - lo) / span if span > 1e-9 else 0.5
            ranked.append((book_id, normalized))
            if len(ranked) >= limit:
                break
        return ranked

    def similar_items(self, book_id: str, limit: int = 10) -> list[tuple[str, float]]:
        """Items whose latent factors resemble this one ('also enjoyed')."""
        if not self.is_trained:
            return []
        idx = self.item_index.get(str(book_id))
        if idx is None:
            return []

        target = self.item_factors[idx]
        norms = np.linalg.norm(self.item_factors, axis=1) * np.linalg.norm(target)
        norms[norms == 0] = 1e-9
        similarity = (self.item_factors @ target) / norms

        out: list[tuple[str, float]] = []
        for candidate in np.argsort(-similarity):
            if candidate == idx:
                continue
            out.append((self._item_ids[candidate], float(similarity[candidate])))
            if len(out) >= limit:
                break
        return out

    # -- Persistence ------------------------------------------------------

    def save(self, directory: str, version: str | None = None) -> str | None:
        """Persist factors as .npz plus a JSON sidecar of the id mappings.

        NOTE: Render's disk is ephemeral — anything written here is lost on
        redeploy, so the model is retrained nightly regardless. For durable
        storage, point this at a mounted disk or push the .npz to Cloudinary
        as a raw resource.
        """
        if not self.is_trained:
            return None
        os.makedirs(directory, exist_ok=True)
        version = version or "latest"
        base = os.path.join(directory, f"als_{version}")

        np.savez_compressed(
            f"{base}.npz",
            user_factors=self.user_factors,
            item_factors=self.item_factors,
        )
        with open(f"{base}.json", "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "user_index": self.user_index,
                    "item_index": self.item_index,
                    "item_ids": self._item_ids,
                    "factors": self.factors,
                    "iterations": self.iterations,
                    "regularization": self.regularization,
                    "alpha": self.alpha,
                    "trained_at": self.trained_at,
                    "metrics": self.metrics,
                },
                fh,
            )
        logger.info("ALS model saved to %s.npz", base)
        return f"{base}.npz"

    @classmethod
    def load(cls, directory: str, version: str = "latest") -> Optional["ALSModel"]:
        base = os.path.join(directory, f"als_{version}")
        if not (os.path.exists(f"{base}.npz") and os.path.exists(f"{base}.json")):
            return None
        try:
            with open(f"{base}.json", encoding="utf-8") as fh:
                meta = json.load(fh)
            arrays = np.load(f"{base}.npz")

            model = cls(
                factors=meta["factors"],
                iterations=meta["iterations"],
                regularization=meta["regularization"],
                alpha=meta["alpha"],
            )
            model.user_factors = arrays["user_factors"]
            model.item_factors = arrays["item_factors"]
            model.user_index = meta["user_index"]
            model.item_index = meta["item_index"]
            model._item_ids = meta["item_ids"]
            model.trained_at = meta.get("trained_at")
            model.metrics = meta.get("metrics", {})
            logger.info("ALS model loaded from %s.npz", base)
            return model
        except Exception as exc:
            logger.error("Failed to load ALS model: %s", exc)
            return None


# --- Offline evaluation --------------------------------------------------

def precision_recall_at_k(
    recommended: Sequence[str], relevant: Sequence[str], k: int = 10
) -> tuple[float, float]:
    if not relevant:
        return 0.0, 0.0
    top_k = list(recommended)[:k]
    hits = len(set(top_k) & set(relevant))
    return hits / max(1, len(top_k)), hits / len(relevant)


def ndcg_at_k(
    recommended: Sequence[str], relevant: Sequence[str], k: int = 10
) -> float:
    """Normalised DCG — rewards putting relevant items near the top."""
    relevant_set = set(relevant)
    dcg = sum(
        1.0 / np.log2(rank + 2)
        for rank, book_id in enumerate(list(recommended)[:k])
        if book_id in relevant_set
    )
    ideal = sum(1.0 / np.log2(rank + 2) for rank in range(min(k, len(relevant_set))))
    return float(dcg / ideal) if ideal > 0 else 0.0
