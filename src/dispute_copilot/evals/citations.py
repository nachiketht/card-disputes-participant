"""Deterministic citation check.

A citation is supported when its source id is in the corpus and the quote
appears in that source text. This does not judge whether the quote supports
the claim.
"""

from dispute_copilot.fields import CASE_ID, CITATION_QUOTE, CITATION_SOURCE_ID, CITATIONS

LIMIT = (
    "This check does not judge semantic support. "
    "It only checks that the source id exists and the quote appears in that source text."
)


class JoinedCorpus:
    """File texts first. Extra texts fill source ids that are not files."""

    def __init__(self, files, extra: dict[str, str]) -> None:
        self._files = files
        known = set(files.ids())
        self._extra = {key: value for key, value in extra.items() if key not in known and isinstance(value, str)}

    def ids(self) -> list[str]:
        return sorted(set(self._files.ids()) | set(self._extra))

    def get(self, source_id: str) -> str:
        if source_id in self._extra:
            return self._extra[source_id]
        return self._files.get(source_id)


def citation_support(corpus, drafts: list[dict]) -> dict:
    known = set(corpus.ids())
    supported = 0
    total = 0
    failures: list[dict] = []
    for draft in drafts:
        case_id = draft.get(CASE_ID) if isinstance(draft, dict) else ""
        citations = draft.get(CITATIONS) if isinstance(draft, dict) else None
        if not isinstance(citations, list):
            continue
        for item in citations:
            total += 1
            source = item.get(CITATION_SOURCE_ID) if isinstance(item, dict) else None
            quote = item.get(CITATION_QUOTE) if isinstance(item, dict) else None
            exists = isinstance(source, str) and source in known
            present = exists and isinstance(quote, str) and bool(quote.strip()) and quote in corpus.get(source)
            if exists and present:
                supported += 1
                continue
            failures.append(
                {
                    "case": case_id if isinstance(case_id, str) else "",
                    "source": source if isinstance(source, str) else "",
                    "reason": "missing_source" if not exists else "quote_absent",
                }
            )
    return {"supported": supported, "total": total, "failures": failures, "limit": LIMIT}
