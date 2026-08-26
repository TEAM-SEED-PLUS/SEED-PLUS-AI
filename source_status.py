"""API source result classification without optional data-source dependencies."""

from typing import Any, Callable


def failed_block(source: str, exc: Exception) -> dict[str, Any]:
    return {
        "count": 0,
        "items": [],
        "source_status": {"status": "failed", "source": source, "reason": str(exc)},
    }


def collect_with_status(source: str, fetcher: Callable[..., Any], *args: Any) -> dict[str, Any]:
    """수집 실패와 정상 0건을 동일한 빈 목록으로 뭉개지 않는다."""
    try:
        block = fetcher(*args)
        if not isinstance(block, dict):
            raise RuntimeError("응답이 object가 아닙니다.")
        existing = block.get("source_status")
        if isinstance(existing, dict):
            return block
        if block.get("fallback_reason"):
            status = "fallback"
            reason = str(block.get("fallback_reason"))
        elif source == "weather":
            status = "ok" if block.get("summary") else "no_data"
            reason = ""
        else:
            items = block.get("items", [])
            status = "ok" if isinstance(items, list) and items else "no_data"
            reason = ""
        block["source_status"] = {"status": status, "source": source}
        if reason:
            block["source_status"]["reason"] = reason
        return block
    except Exception as exc:
        return failed_block(source, exc)
