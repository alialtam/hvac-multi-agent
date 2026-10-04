import re
from functools import lru_cache
from pathlib import Path
from rank_bm25 import BM25Okapi

ROOT = Path(__file__).parent


def _tok(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


@lru_cache(maxsize=1)
def _index():
    docs = []
    for kind, folder in (("manual", "manuals"), ("incident", "incidents")):
        for p in sorted((ROOT / folder).glob("*.md")):
            text = p.read_text(encoding="utf-8")
            title = text.splitlines()[0].lstrip("# ").strip()
            docs.append({"id": p.stem, "title": title, "source": f"{kind}: {title}", "text": text})
    return docs, BM25Okapi([_tok(d["text"]) for d in docs])


def search_knowledge(query: str, k: int = 3) -> list[dict]:
    docs, bm25 = _index()
    scores = bm25.get_scores(_tok(query))
    top = sorted(range(len(docs)), key=lambda i: scores[i], reverse=True)[:k]
    return [{**docs[i], "score": float(scores[i])} for i in top if scores[i] > 0]