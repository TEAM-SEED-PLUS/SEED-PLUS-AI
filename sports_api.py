import json
from typing import Any
import argparse
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from common import (
        SPORTS_STADIUM_DISTRICT_HINTS,
        build_query_context,
        build_standard_parser,
        classify_time_band,
        normalize_text,
    )


# 공통
KBO_URL = "https://www.koreabaseball.com/Schedule/Schedule.aspx"
KLEAGUE_URL = "https://www.kleague.com/schedule.do"
KBL_URL = "https://www.kbl.or.kr/match/schedule"

KLEAGUE_LEAGUES = {
    1: "K League 1",
    2: "K League 2",
}

KLEAGUE_BROADCAST_WORDS = {
    "COUPANGPLAY", "IB SPORTS", "SKY SPORTS", "JTBC", "MAXPORTS",
    "쿠팡플레이", "스카이스포츠"
}

KBL_VENUE_KEYWORDS = ("체육관", "아레나", "arena", "gymnasium")
KBL_BROADCAST_KEYWORDS = ("TVING", "SPOTV", "KBS", "MBC", "SBS", "IB SPORTS", "중계")
KBL_IGNORE_EXACT = {
    "일정 및 결과", "팀순위", "기록", "선수", "티켓", "스토어",
    "KBL", "로그인", "회원가입", "ENGLISH"
}


def get_default_date() -> datetime:
    return datetime.now(ZoneInfo("Asia/Seoul"))


def parse_target_date(date_str: str | None) -> datetime:
    if not date_str:
        return get_default_date()

    s = str(date_str).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=ZoneInfo("Asia/Seoul"))
        except ValueError:
            continue

    raise ValueError("날짜 형식이 올바르지 않습니다. 예: 2026-04-08 또는 20260408")


def clean_text(text):
    if text is None:
        return ""
    text = str(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def digits8(text):
    s = re.sub(r"\D", "", clean_text(text))
    return s[:8] if len(s) >= 8 else ""


def is_time_text(text):
    return bool(re.fullmatch(r"\d{1,2}:\d{2}", clean_text(text)))


def block_common_resources(route):
    resource_type = route.request.resource_type
    if resource_type in {"image", "font", "media", "stylesheet"}:
        route.abort()
    else:
        route.continue_()


def create_fast_context(browser):
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        locale="ko-KR",
        java_script_enabled=True,
    )
    context.route("**/*", block_common_resources)
    page = context.new_page()
    page.set_default_timeout(15000)
    return context, page


# Basketball (KBO)
def build_kbo_url(target_date: datetime) -> str:
    year = target_date.strftime("%Y")
    month = target_date.strftime("%m")
    return f"{KBO_URL}?leagueId=1&year={year}&month={month}"


def is_same_day_kbo_row(current_date_text: str, target_date: datetime) -> bool:
    compact = current_date_text.replace(" ", "")
    month = target_date.month
    day = target_date.day
    year = target_date.year

    patterns = [
        f"{month:02d}.{day:02d}",
        f"{month}.{day}",
        f"{year}.{month:02d}.{day:02d}",
        f"{year}.{month}.{day}",
        f"{year}-{month:02d}-{day:02d}",
        f"{year}/{month:02d}/{day:02d}",
    ]
    return any(p in compact for p in patterns)


def normalize_kbo_match_text(match_text: str) -> str:
    """
    예:
    - 한화0vs8두산 -> 한화 vs 두산
    - SSG4vs3롯데 -> SSG vs 롯데
    - 삼성 0 : 2 KT -> 삼성 vs KT
    """
    text = re.sub(r"\s+", "", match_text)
    m = re.match(r"^([^\d]+)\d+\s*(?:vs|VS|:)\s*\d+([^\d]+)$", text)
    if m:
        home = m.group(1).strip()
        away = m.group(2).strip()
        return f"{home} vs {away}"
    return match_text.strip()


def parse_kbo_schedule_rows(rows_texts: list[list[str]], target_date: datetime) -> list[dict]:
    games = []
    current_date = None

    for texts in rows_texts:
        texts = [t.strip() for t in texts if t is not None]
        if not texts or not any(texts):
            continue

        if len(texts) >= 9:
            row_date = texts[0]
            row_time = texts[1]
            row_match = texts[2]
            row_stadium = texts[7]
            if row_date:
                current_date = row_date
        elif len(texts) >= 8:
            row_date = current_date
            row_time = texts[0]
            row_match = texts[1]
            row_stadium = texts[6]
        else:
            continue

        if not row_date:
            continue

        if is_same_day_kbo_row(row_date, target_date):
            games.append({
                "sport": "Baseball",
                "league": "KBO",
                "date": target_date.strftime("%Y-%m-%d"),
                "time": row_time,
                "match": normalize_kbo_match_text(row_match),
                "stadium": row_stadium or "-",
            })

    return games


def get_kbo_schedule(browser, target_date: datetime) -> list[dict]:
    context, page = create_fast_context(browser)
    try:
        page.goto(build_kbo_url(target_date), wait_until="domcontentloaded", timeout=15000)
        page.wait_for_selector("table tbody tr", timeout=8000)

        rows = page.locator("table tbody tr")
        row_count = rows.count()

        rows_texts = []
        for i in range(row_count):
            rows_texts.append(rows.nth(i).locator("td").all_inner_texts())

        return parse_kbo_schedule_rows(rows_texts, target_date)

    except PlaywrightTimeoutError:
        return []
    finally:
        context.close()


# Soccer (K League)
def kleague_get_month_param(target_dt):
    year = target_dt.year
    month_param = target_dt.month - 1
    if month_param < 1:
        month_param = 12
        year -= 1
    return year, month_param


def kleague_extract_team_name(team_div):
    if team_div is None:
        return ""

    name_span = team_div.select_one("span.name")
    if name_span:
        name = clean_text(name_span.get_text(" ", strip=True))
        if name:
            return name

    img = team_div.find("img")
    if img and img.get("alt"):
        name = clean_text(img.get("alt"))
        if name:
            return name

    a = team_div.find("a")
    if a and a.get("title"):
        name = clean_text(a.get("title"))
        if name:
            return name

    return ""


def kleague_extract_match_from_td(match_td):
    if match_td is None:
        return ""

    team_divs = match_td.select("div.team")
    teams = []
    for div in team_divs:
        team_name = kleague_extract_team_name(div)
        if team_name:
            teams.append(team_name)

    deduped = []
    for t in teams:
        if t not in deduped:
            deduped.append(t)

    if len(deduped) >= 2:
        return f"{deduped[0]} vs {deduped[1]}"

    text = clean_text(match_td.get_text(" ", strip=True))
    text = re.sub(r"(?i)\bmatch\s*center\b", "", text)
    return clean_text(text)


def kleague_is_candidate_schedule_table(table):
    html = str(table)
    text = clean_text(table.get_text(" ", strip=True))
    if not text:
        return False

    has_date = bool(re.search(r"\d{4}\.\d{2}\.\d{2}", text))
    has_match_center = (
        "매치센터" in text
        or "Match Center" in text
        or "btn-match" in html
    )
    has_schedule_structure = (
        "match-wrap" in html
        or "calendar__day" in html
        or 'class="team"' in html
        or "div class=\"team\"" in html
    )
    return has_date and has_match_center and has_schedule_structure


def kleague_get_direct_tds(tr):
    return tr.find_all("td", recursive=False)


def kleague_find_button_td_index(tds):
    for idx, td in enumerate(tds):
        if td.select_one("button.btn-match"):
            return idx
    return -1


def kleague_find_time_from_tds(tds):
    for td in tds:
        txt = clean_text(td.get_text(" ", strip=True))
        if is_time_text(txt):
            return txt
    return ""


def kleague_find_date_from_tds(tds):
    for td in tds:
        txt = clean_text(td.get_text(" ", strip=True))
        if bool(re.search(r"\d{4}\.\d{2}\.\d{2}", txt)):
            return txt
    return ""


def kleague_find_match_td(tds):
    for td in tds:
        if len(td.select("div.team")) >= 2:
            return td
    return None


def kleague_extract_stadium_from_tds(tds):
    btn_idx = kleague_find_button_td_index(tds)
    if btn_idx > 0:
        return clean_text(tds[btn_idx - 1].get_text(" ", strip=True))

    texts = [clean_text(td.get_text(" ", strip=True)) for td in tds]
    for txt in reversed(texts):
        low = txt.lower()
        if not txt:
            continue
        if "매치센터" in txt or "match center" in low:
            continue
        if is_time_text(txt):
            continue
        if bool(re.search(r"\d{4}\.\d{2}\.\d{2}", txt)):
            continue
        if txt in KLEAGUE_BROADCAST_WORDS:
            continue
        if "경기전" in txt or "경기종료" in txt:
            continue
        return txt
    return ""


def kleague_extract_relevant_html(full_html):
    table_pattern = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)
    tables = table_pattern.findall(full_html)

    selected = []
    for table_html in tables:
        if "btn-match" not in table_html:
            continue
        if not re.search(r"\d{4}\.\d{2}\.\d{2}", table_html):
            continue
        selected.append(table_html)

    if selected:
        return "\n".join(selected)
    return full_html


def parse_kleague_schedule_tables(html, league_name, target_dt: datetime) -> list[dict]:
    target_digits = target_dt.strftime("%Y%m%d")
    target_csv_date = target_dt.strftime("%Y-%m-%d")

    relevant_html = kleague_extract_relevant_html(html)
    try:
        soup = BeautifulSoup(relevant_html, "lxml")
    except Exception:
        soup = BeautifulSoup(relevant_html, "html.parser")

    candidate_tables = [t for t in soup.find_all("table") if kleague_is_candidate_schedule_table(t)]
    results = []
    seen = set()

    for table in candidate_tables:
        tbody = table.find("tbody")
        if not tbody:
            continue

        current_date = ""
        for tr in tbody.find_all("tr", recursive=False):
            tds = kleague_get_direct_tds(tr)
            if not tds:
                continue

            row_date = kleague_find_date_from_tds(tds)
            if row_date:
                current_date = row_date

            btn_idx = kleague_find_button_td_index(tds)
            if btn_idx == -1:
                continue

            if digits8(current_date) != target_digits:
                continue

            time_text = kleague_find_time_from_tds(tds)
            match_td = kleague_find_match_td(tds)
            match_text = kleague_extract_match_from_td(match_td)
            stadium_text = kleague_extract_stadium_from_tds(tds)

            if not match_text:
                continue

            key = (league_name, target_csv_date, time_text, match_text, stadium_text)
            if key in seen:
                continue
            seen.add(key)

            results.append({
                "sport": "Soccer",
                "league": league_name,
                "date": target_csv_date,
                "time": time_text,
                "match": match_text,
                "stadium": stadium_text,
            })

    return results


def get_kleague_schedule(browser, target_dt: datetime) -> list[dict]:
    all_rows = []
    context, page = create_fast_context(browser)

    try:
        for league_id, league_name in KLEAGUE_LEAGUES.items():
            year, month_param = kleague_get_month_param(target_dt)
            url = f"{KLEAGUE_URL}?leagueId={league_id}&year={year}&month={month_param}"

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                try:
                    page.wait_for_selector("button.btn-match, ul.match-wrap, .calendar__day", timeout=1500)
                except PlaywrightTimeoutError:
                    pass

                try:
                    page.wait_for_load_state("networkidle", timeout=1200)
                except PlaywrightTimeoutError:
                    pass

                html = page.content()
                all_rows.extend(parse_kleague_schedule_tables(html, league_name, target_dt))
            except Exception:
                continue

        return all_rows
    finally:
        context.close()


# Basketball (KBL)
def normalize_kbl_target_date(target: datetime):
    return {
        "dt": target,
        "year": target.strftime("%Y"),
        "month": target.strftime("%m"),
        "day": target.strftime("%d"),
        "display": target.strftime("%Y-%m-%d"),
    }


def kbl_target_date_tokens(target: dict) -> list[str]:
    y = int(target["year"])
    m = int(target["month"])
    d = int(target["day"])
    return [
        f"{m:02d}.{d:02d}",
        f"{m}.{d}",
        f"{y}.{m:02d}.{d:02d}",
        f"{y}.{m}.{d}",
        f"{y}-{m:02d}-{d:02d}",
        f"{y}/{m:02d}/{d:02d}",
    ]


def kbl_is_target_date_text(text: str, target: dict) -> bool:
    compact = text.replace(" ", "")
    return any(tok in compact for tok in kbl_target_date_tokens(target))


def kbl_is_date_header(line: str) -> bool:
    compact = line.replace(" ", "")
    patterns = [
        r"^(?:20\d{2}[./-])?\d{1,2}[./-]\d{1,2}(?:\([월화수목금토일]\))?$",
        r"^(?:20\d{2}[./-])?\d{1,2}[./-]\d{1,2}[ ]?[월화수목금토일]$",
    ]
    return any(re.match(p, compact) for p in patterns)


def kbl_extract_time(line: str) -> str:
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", line)
    return m.group(0) if m else ""


def kbl_looks_like_score_or_noise(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if s in KBL_IGNORE_EXACT:
        return True
    if re.fullmatch(r"[\d\s:./()-]+", s):
        return True
    if re.fullmatch(r"\d{1,3}\s*[:\-]\s*\d{1,3}", s):
        return True
    return False


def kbl_extract_section_lines(body_text: str, target: dict) -> list[str]:
    lines = [clean_text(x) for x in body_text.splitlines()]
    lines = [x for x in lines if x]

    section = []
    in_target_section = False

    for line in lines:
        if kbl_is_target_date_text(line, target):
            if not in_target_section:
                in_target_section = True
                section.append(line)
                continue

        if in_target_section:
            if kbl_is_date_header(line) and not kbl_is_target_date_text(line, target):
                break
            section.append(line)

    return section


def kbl_finalize_game(time_text: str, chunks: list[str], target: dict) -> dict | None:
    if not time_text:
        return None

    cleaned = []
    for c in chunks:
        c = clean_text(c)
        if not c:
            continue
        if c in KBL_IGNORE_EXACT:
            continue
        cleaned.append(c)

    stadium = ""
    info = []

    for c in cleaned:
        low = c.lower()
        if any(k.lower() in low for k in KBL_VENUE_KEYWORDS):
            if not stadium:
                stadium = c
            continue
        if any(k.lower() in low for k in KBL_BROADCAST_KEYWORDS):
            continue
        if not kbl_looks_like_score_or_noise(c):
            info.append(c)

    match_text = ""
    for c in info:
        m = re.search(r"([가-힣A-Za-z0-9 ]+?)\s*(?:vs|VS|:)\s*([가-힣A-Za-z0-9 ]+)", c)
        if m:
            match_text = f"{m.group(1).strip()} vs {m.group(2).strip()}"
            break

    if not match_text:
        team_lines = []
        for c in info:
            if any(k.lower() in c.lower() for k in KBL_VENUE_KEYWORDS):
                continue
            if any(k.lower() in c.lower() for k in KBL_BROADCAST_KEYWORDS):
                continue
            if kbl_extract_time(c):
                continue
            team_lines.append(c)

        team_lines = [x for x in team_lines if len(x) <= 30]

        if len(team_lines) >= 2:
            match_text = f"{team_lines[0]} vs {team_lines[1]}"
        elif len(team_lines) == 1:
            match_text = team_lines[0]

    if not match_text:
        return None

    return {
        "sport": "Basketball",
        "league": "KBL",
        "date": target["display"],
        "time": time_text,
        "match": match_text,
        "stadium": stadium or "-",
    }


def kbl_parse_games_from_section(section_lines: list[str], target: dict) -> list[dict]:
    games = []
    current_time = ""
    chunks = []

    for line in section_lines:
        if kbl_is_date_header(line):
            continue

        t = kbl_extract_time(line)
        if t:
            prev = kbl_finalize_game(current_time, chunks, target)
            if prev and prev not in games:
                games.append(prev)

            current_time = t
            rest = clean_text(line.replace(t, " "))
            chunks = [rest] if rest else []
            continue

        if current_time:
            chunks.append(line)

    prev = kbl_finalize_game(current_time, chunks, target)
    if prev and prev not in games:
        games.append(prev)

    return games


def get_kbl_schedule(browser, target_date: datetime) -> list[dict]:
    target = normalize_kbl_target_date(target_date)
    context, page = create_fast_context(browser)

    try:
        page.goto(KBL_URL, wait_until="domcontentloaded", timeout=20000)
        try:
            page.wait_for_load_state("networkidle", timeout=6000)
        except PlaywrightTimeoutError:
            pass

        body_text = ""
        for _ in range(8):
            try:
                body_text = page.locator("body").inner_text(timeout=3000)
            except Exception:
                body_text = ""
            if kbl_is_target_date_text(body_text, target):
                break
            page.wait_for_timeout(250)

        section_lines = kbl_extract_section_lines(body_text, target)
        return kbl_parse_games_from_section(section_lines, target)

    except PlaywrightTimeoutError:
        return []
    finally:
        context.close()


# 통합
def print_section(title: str, rows: list[dict]):
    if not rows:
        print(f"{title}: 0경기")
        return
    print(f"{title}: {len(rows)}경기")
    for i, row in enumerate(rows, 1):
        print(f"  [{i}] {row['time']} | {row['match']} | 경기장: {row['stadium']}")


def main():
    parser = argparse.ArgumentParser(description="야구/축구/농구 통합 일정 크롤러")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="조회 날짜 (예: 2026-04-19 또는 20260419). 없으면 오늘 날짜",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="저장할 CSV 파일명. 없으면 sports_schedule_YYYYMMDD.csv",
    )
    args = parser.parse_args()

    target_date = parse_target_date(args.date)
    output_file = (
        args.output
        if args.output
        else f"sports_schedule_{target_date.strftime('%Y%m%d')}.csv"
    )

    all_rows = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )

        try:
            kbo_rows = get_kbo_schedule(browser, target_date)
            kleague_rows = get_kleague_schedule(browser, target_date)
            kbl_rows = get_kbl_schedule(browser, target_date)
        finally:
            browser.close()

    all_rows.extend(kbo_rows)
    all_rows.extend(kleague_rows)
    all_rows.extend(kbl_rows)

    df = pd.DataFrame(all_rows, columns=["sport", "league", "date", "time", "match", "stadium"])

    if len(df):
        df = (
            df.drop_duplicates()
              .sort_values(by=["sport", "league", "time", "match"], kind="stable")
              .reset_index(drop=True)
        )

    df.to_csv(output_file, index=False, encoding="utf-8-sig")

    label = target_date.strftime("%Y-%m-%d")
    print("=" * 100)
    print(f"통합 경기 일정 ({label})")
    print("=" * 100)
    print_section("⚾ KBO", kbo_rows)
    print_section("⚽ K League", kleague_rows)
    print_section("🏀 KBL", kbl_rows)
    print("=" * 100)
    print(f"총 수집 경기 수: {len(df)}")
    print(f"CSV 저장 완료: {output_file}")


def infer_district_from_stadium(stadium: str) -> str:
    s = normalize_text(stadium).lower()
    for keyword, district in SPORTS_STADIUM_DISTRICT_HINTS.items():
        if keyword.lower() in s:
            return district
    return ""


def get_sports(
    district: str,
    date_str: str | None = None,
    time_str: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    ctx = build_query_context(district, date_str, time_str)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        try:
            rows = []
            rows.extend(get_kbo_schedule(browser, ctx.target_datetime))
            rows.extend(get_kleague_schedule(browser, ctx.target_datetime))
            rows.extend(get_kbl_schedule(browser, ctx.target_datetime))
        finally:
            browser.close()

    items = []
    for row in rows:
        stadium = normalize_text(row.get("stadium"))
        stadium_district = infer_district_from_stadium(stadium)
        if stadium_district != ctx.district_ko:
            continue

        row_time = normalize_text(row.get("time"))
        try:
            band = classify_time_band(build_query_context(ctx.district_ko, ctx.date_str, row_time).target_datetime.time())
        except Exception:
            band = ctx.time_band
        if band != ctx.time_band:
            continue

        items.append({
            "source": row.get("league"),
            "sport": row.get("sport"),
            "league": row.get("league"),
            "date": row.get("date"),
            "time": row.get("time"),
            "match": row.get("match"),
            "stadium": stadium,
            "district": stadium_district,
        })

    items.sort(key=lambda x: (x.get("time") or "", x.get("league") or "", x.get("match") or ""))
    return {
        "district": ctx.district_ko,
        "date": ctx.date_str,
        "time": ctx.time_str,
        "time_band": ctx.time_band,
        "count": len(items),
        "items": items[:limit],
    }


def main() -> None:
    parser = build_standard_parser("스포츠 API 조회")
    args = parser.parse_args()
    result = get_sports(args.district, args.date, args.time, args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
