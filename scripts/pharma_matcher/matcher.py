import json
import urllib.request
import re
import numpy as np
from typing import List, Dict, Optional, Tuple
from .normalizer import clean_text, extract_dosage, extract_pack_count, extract_keywords

class PharmaMatcher:
    def __init__(self, lmstudio_url: str = "http://localhost:1234/v1"):
        self.lmstudio_url = lmstudio_url
        self.embedding_cache = {}

    def get_embeddings(self, texts: List[str], batch_size: int = 15) -> np.ndarray:
        """Batch embedding request to LM Studio with caching."""
        uncached_texts = [t for t in texts if t not in self.embedding_cache]
        if uncached_texts:
            for i in range(0, len(uncached_texts), batch_size):
                batch = uncached_texts[i:i+batch_size]
                payload = json.dumps({
                    "model": "text-embedding-nomic-embed-text-v1.5",
                    "input": batch
                }).encode("utf-8")
                req = urllib.request.Request(
                    f"{self.lmstudio_url}/embeddings",
                    data=payload,
                    headers={"Content-Type": "application/json"}
                )
                try:
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        data = json.loads(resp.read().decode())
                        sorted_items = sorted(data['data'], key=lambda x: x['index'])
                        for text, item in zip(batch, sorted_items):
                            self.embedding_cache[text] = item['embedding']
                except Exception as e:
                    for text in batch:
                        self.embedding_cache[text] = [0.0] * 768

        vecs = np.array([self.embedding_cache[t] for t in texts])
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms

    def score_pair(
        self,
        local_name: str,
        cand_name: str,
        cand_dose: Optional[str] = None,
        cand_pack: Optional[int] = None,
        vec_sim: float = 0.0
    ) -> Tuple[float, str, bool]:
        """Compute matching score with dosage & pack validation."""
        local_dose = extract_dosage(local_name)
        local_pack = extract_pack_count(local_name)

        cand_dose_val = extract_dosage(cand_dose) if cand_dose else extract_dosage(cand_name)
        cand_pack_val = cand_pack if cand_pack is not None else extract_pack_count(cand_name)

        # 1. Dosage match check
        dose_match = None
        if local_dose and cand_dose_val:
            dose_match = (local_dose == cand_dose_val)

        # 2. Pack match check
        pack_match = None
        if local_pack and cand_pack_val:
            pack_match = (local_pack == cand_pack_val)

        # 3. Containment overlap (how much of local text is covered by candidate)
        t1 = set(re.findall(r'[а-яёa-z0-9]+', clean_text(local_name).lower()))
        t2 = set(re.findall(r'[а-яёa-z0-9]+', clean_text(cand_name).lower()))
        
        # Stop words to ignore in token overlap
        stopwords = {'таб', 'таблетки', 'капс', 'капсулы', 'пач', 'картон', 'уп', 'контурн', 'яч', 'бл', 'в', 'и', 'для'}
        t1_filtered = t1 - stopwords
        t2_filtered = t2 - stopwords
        
        containment = len(t1_filtered & t2_filtered) / len(t1_filtered) if t1_filtered else 0.0
        jaccard = len(t1 & t2) / len(t1 | t2) if (t1 | t2) else 0.0
        text_score = 0.7 * containment + 0.3 * jaccard

        # Base hybrid score: 55% vector + 25% text coverage
        score = 0.55 * vec_sim + 0.25 * text_score

        # Guardrails on dosage and packaging
        if dose_match is True:
            score += 0.15
        elif dose_match is False:
            score -= 0.35  # Penalty for wrong dosage

        if pack_match is True:
            score += 0.05
        elif pack_match is False:
            score -= 0.05

        score = max(0.0, min(1.0, score))

        # Status determination
        if dose_match is False:
            status = "DOSE_MISMATCH"
        elif score >= 0.78:
            status = "AUTO_MATCH"
        elif score >= 0.60:
            status = "NEEDS_REVIEW"
        else:
            status = "LOW_CONFIDENCE"

        return score, status, bool(dose_match is not False)

    def match_item(self, local_name: str, candidates: List[Dict]) -> Dict:
        """Find best matching candidate from a pre-filtered list."""
        if not candidates:
            return {
                "local_name": local_name,
                "status": "NO_CANDIDATES",
                "score": 0.0,
                "best_match": None,
                "top_candidates": []
            }

        cand_names = [c["name"] for c in candidates]
        all_vecs = self.get_embeddings([local_name] + cand_names)
        local_vec = all_vecs[0]
        cand_vecs = all_vecs[1:]

        vec_sims = np.dot(cand_vecs, local_vec)

        scored_candidates = []
        for idx, cand in enumerate(candidates):
            sim = float(vec_sims[idx])
            score, status, valid_dose = self.score_pair(
                local_name=local_name,
                cand_name=cand["name"],
                cand_dose=cand.get("dose"),
                cand_pack=cand.get("pack"),
                vec_sim=sim
            )
            scored_candidates.append({
                "id": cand["id"],
                "name": cand["name"],
                "score": round(score, 4),
                "vec_sim": round(sim, 4),
                "status": status,
                "dose_valid": valid_dose
            })

        scored_candidates.sort(key=lambda x: x["score"], reverse=True)
        best = scored_candidates[0]

        return {
            "local_name": local_name,
            "status": best["status"],
            "score": best["score"],
            "best_match": best,
            "top_candidates": scored_candidates[:3]
        }
