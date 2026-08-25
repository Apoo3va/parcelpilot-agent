from pathlib import Path
from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

DOCS_DIR = Path(__file__).parent.parent / "data" / "docs"

DOC_METADATA = {
    "01_Support_Policy_v3_CURRENT.pdf": {
        "doc_type": "current_policy", "authority_rank": 2, "account_id": None,
        "label": "Support Policy v3 (CURRENT, effective 1 May 2026)"
    },
    "02_Support_Policy_v2_DEPRECATED.pdf": {
        "doc_type": "deprecated_policy", "authority_rank": 4, "account_id": None,
        "label": "Support Policy v2 (DEPRECATED — do not use as current authority)"
    },
    "03_Cancellation_and_Service_Credit_SOP_v4.pdf": {
        "doc_type": "current_sop", "authority_rank": 2, "account_id": None,
        "label": "Cancellation & Service Credit SOP v4 (CURRENT)"
    },
    "04_Product_Operations_Guide_and_Known_Issues.pdf": {
        "doc_type": "product_ops", "authority_rank": 2, "account_id": None,
        "label": "Product Operations Guide & Known Issues (updated 14 Aug 2026)"
    },
    "05_Northstar_Logistics_Enterprise_Agreement.pdf": {
        "doc_type": "agreement", "authority_rank": 1, "account_id": "ACCT-001",
        "label": "Northstar Logistics Enterprise Agreement"
    },
    "06_LumenWorks_Service_Agreement.pdf": {
        "doc_type": "agreement", "authority_rank": 1, "account_id": "ACCT-002",
        "label": "LumenWorks Service Agreement"
    },
}

class DocIndex:
    def __init__(self):
        self.chunks = []
        self.vectorizer = None
        self.matrix = None
        self._build()

    def _build(self):
        for fname, meta in DOC_METADATA.items():
            path = DOCS_DIR / fname
            reader = PdfReader(str(path))
            full_text = "\n".join(page.extract_text() or "" for page in reader.pages)
            raw_chunks = [c.strip() for c in full_text.split("\n\n") if c.strip()]
            buf = ""
            for c in raw_chunks:
                buf += c + "\n"
                if len(buf) > 400:
                    self.chunks.append({"text": buf.strip(), "source_file": fname, **meta})
                    buf = ""
            if buf.strip():
                self.chunks.append({"text": buf.strip(), "source_file": fname, **meta})

        corpus = [c["text"] for c in self.chunks]
        self.vectorizer = TfidfVectorizer(stop_words="english")
        self.matrix = self.vectorizer.fit_transform(corpus)

    def search(self, query, account_id, top_k=5, include_deprecated=False):
        qvec = self.vectorizer.transform([query])
        sims = cosine_similarity(qvec, self.matrix)[0]
        results = []
        for i, score in enumerate(sims):
            chunk = self.chunks[i]
            if chunk["doc_type"] == "deprecated_policy" and not include_deprecated:
                continue
            if chunk["doc_type"] == "agreement" and account_id is not None \
               and chunk["account_id"] != account_id:
                continue
            results.append((score, chunk))
        results.sort(key=lambda x: x[0], reverse=True)
        top = results[:top_k]
        return [
            {
                "text": c["text"], "source": c["label"], "doc_type": c["doc_type"],
                "authority_rank": c["authority_rank"], "relevance": round(float(s), 3),
            }
            for s, c in top if s > 0.03
        ]

_index = None
def get_index():
    global _index
    if _index is None:
        _index = DocIndex()
    return _index