"""Explicit batch for Seoul-wide administrative-dong quarterly snapshots."""
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv

from common import SEOUL_TZ
from snapshot_cache import write_snapshot

SERVICES = {"stores": ("VwsmAdstrdStorW", "SEOUL_DONG_STORE_API_KEY"),
            "sales": ("VwsmAdstrdSelngW", "SEOUL_DONG_SALES_API_KEY")}


class CollectionError(RuntimeError):
    pass


def fetch_quarter(kind, quarter, *, session=requests, page_size=1000):
    service, key_name = SERVICES[kind]
    key = os.getenv(key_name)
    if not key:
        raise CollectionError("credentials_missing")
    base = os.getenv("SEOUL_DONG_API_BASE") or "https://openapi.seoul.go.kr:8088"
    rows, seen, total = [], set(), None
    while total is None or len(rows) < total:
        start = len(rows) + 1
        url = f"{base}/{quote(key, safe='')}/json/{service}/{start}/{start + page_size - 1}/{quarter}"
        try:
            response = session.get(url, timeout=15, allow_redirects=False)
            if response.status_code != 200:
                raise CollectionError(f"http_{response.status_code}")
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise CollectionError(type(exc).__name__) from None
        block = payload.get(service, {})
        result = block.get("RESULT", payload.get("RESULT", {}))
        code = result.get("CODE")
        has_message = isinstance(result.get("MESSAGE"), str) and bool(result["MESSAGE"].strip())
        if code == "INFO-200" and has_message and not rows:
            return []
        if code != "INFO-000" or not has_message:
            raise CollectionError("api_authentication" if code in ("ERROR-300", "ERROR-301")
                                  else "api_result_error")
        count, batch = block.get("list_total_count"), block.get("row")
        if not isinstance(count, int) or not isinstance(batch, list) or not batch:
            raise CollectionError("invalid_page")
        if total is not None and total != count:
            raise CollectionError("count_changed_during_pagination")
        total = count
        for row in batch:
            identity = tuple(str(row.get(field, "")) for field in
                             ("STDR_YYQU_CD", "ADSTRD_CD", "SVC_INDUTY_CD"))
            if identity[0] != quarter or not all(identity) or identity in seen:
                raise CollectionError("invalid_or_duplicate_row")
            seen.add(identity)
        rows.extend(batch)
    if len(rows) != total:
        raise CollectionError("count_mismatch")
    return rows


def main():
    load_dotenv()
    now = datetime.now(SEOUL_TZ)
    current = now.year * 4 + (now.month - 1) // 3
    root = Path("data/hongdae/quarterly")
    for kind in SERVICES:
        try:
            for index in range(current, current - 5, -1):
                quarter = f"{index // 4}{index % 4 + 1}"
                rows = fetch_quarter(kind, quarter)
                if rows:
                    write_snapshot(root / f"{kind}_{quarter}.json",
                                   {"rows": rows, "quarter": quarter, "collected_at": now.isoformat()})
                    print(kind, quarter, len(rows))
        except CollectionError as exc:
            print(kind, str(exc))


if __name__ == "__main__":
    main()
