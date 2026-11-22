"""Generate immutable, date-scoped Korean market reports."""
import argparse
import fcntl
import json
import os
import re
import shutil
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sources import CODE, CORP, MARKETS, Live, SourceError, day_value, filings, krx_rows

KST = timezone(timedelta(hours=9))


def checked(operation, *args):
    try:
        return operation(*args), None
    except SourceError as exc:
        return {}, str(exc)


def load_config(path, day):
    if not path:
        return {}, None
    config = json.loads(path.read_text(encoding="utf-8"))
    closed = config.get("closed_days", {}).get(day)
    if closed is not None and (not isinstance(closed, str) or not closed.strip()):
        raise ValueError("휴장일 근거 필요")
    links = {}
    for row in config.get("issuer_links", []):
        start, end = day_value(row["from"]), day_value(row["through"])
        if end < start or not row["source"].strip():
            raise ValueError("발행사 연결 기간 또는 근거 오류")
        if start <= day_value(day) <= end:
            key = (row["market"], row["code"])
            if (key in links or key[0] not in MARKETS or not CODE.fullmatch(key[1])
                    or not CORP.fullmatch(row["corp_code"])):
                raise ValueError("발행사 연결 중복 또는 코드 오류")
            links[key] = row
    return links, closed


def issuer_link(code, explicit, receipts, directory_rows, day):
    # A receipt is dated evidence. Directory changes after the target date are unusable.
    candidates = set(receipts)
    if explicit:
        candidates.add(explicit["corp_code"])
    if len(candidates) > 1:
        return None, "연결 근거 충돌"
    if candidates:
        return candidates.pop(), explicit["source"] if explicit else "당일 공시 종목코드"
    if len(directory_rows) == 1 and directory_rows[0][1] <= day:
        return directory_rows[0][0], "고유번호 목록 대표 종목코드"
    return None, "발행사 미확인"


def correction(row):
    return bool(re.match(r"^\s*\[(?:기재정정|첨부정정|정정)\]", row["report_nm"]))



def build(source, day, links, closed):
    directory, directory_error = checked(source.companies)
    summary = {"date": day, "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
               "errors": {}, "markets": {}, "complete": True,
               "source": "합성 오프라인 예제" if getattr(source, "synthetic", False) else "한국거래소 통계정보 · 금융감독원 OpenDART"}
    if directory_error:
        summary["errors"]["OpenDART 고유번호"] = directory_error
    securities, issuers = {}, {}
    for market, (_, cls) in MARKETS.items():
        errors = {}
        def fetch_krx(kind):
            return krx_rows(source.krx(market, kind, day), market, kind, day)
        masters, base_error = checked(fetch_krx, "master")
        trades, trade_error = checked(fetch_krx, "trade")
        docs, dart_error = checked(filings, source.page, day, cls)
        for label, err in (("종목기본정보", base_error), ("일별매매정보", trade_error), ("공시검색", dart_error)):
            if err:
                errors[label] = err
        receipts_by_code = defaultdict(set)
        market_issuers = {}
        for item in docs.values():
            corp = item["corp_code"]
            receipts_by_code[item.get("stock_code", "")].add(corp)
            market_issuers.setdefault(corp, []).append(item)
        # Market classifications on filings are independently collected and counted.
        for corp, items in market_issuers.items():
            issuers.setdefault(corp, []).extend(items)
        if trades:
            session = "거래일"
            if closed or day_value(day).weekday() >= 5:
                errors["거래일 판정"] = "휴장 근거와 당일 매매행 충돌"
        elif closed or day_value(day).weekday() >= 5:
            session = "휴장"
        else:
            session = "미확인"
        matched = masters.keys() & trades.keys()
        quote_only = trades.keys() - masters.keys()
        comparable = not base_error and not trade_error and bool(masters) and session == "거래일"
        coverage = round(100 * len(matched) / len(masters), 2) if comparable else None
        rows = {}
        for code in sorted(masters.keys() | trades.keys()):
            corp, evidence = issuer_link(code, links.get((market, code)), receipts_by_code[code],
                                         directory.get(code, []), day)
            rows[code] = {"date": day, "market": market, "code": code, "master": masters.get(code),
                          "trade": trades.get(code), "corp_code": corp, "issuer_link": evidence,
                          "issuer_filing_count": None, "issuer_receipts": None}
        securities[market] = rows
        linked = {r["corp_code"] for r in rows.values() if r["corp_code"]}
        summary["markets"][market] = {
            "session": session, "closed_evidence": (closed or "토·일요일") if session == "휴장" else None,
            "master": None if base_error else len(masters), "trade": None if trade_error else len(trades),
            "matched": len(matched) if comparable else None, "coverage_pct": coverage,
            "missing_trade": len(masters) - len(matched) if comparable else None,
            "trade_only": len(quote_only) if not base_error and not trade_error else None,
            "unlinked_securities": sum(r["corp_code"] is None for r in rows.values()),
            "unmatched_filing_issuers": sorted(market_issuers.keys() - linked) if not dart_error else None,
            "filings": None if dart_error else len(docs), "filing_issuers": None if dart_error else len(market_issuers),
            "corrections": None if dart_error else sum(correction(r) for r in docs.values()), "errors": errors}
        if errors or session == "미확인" or (session == "거래일" and (not comparable or
                len(matched) != len(masters) or quote_only)):
            summary["complete"] = False
    all_filings_known = all(m["filings"] is not None for m in summary["markets"].values())
    for rows in securities.values():
        for row in rows.values():
            corp = row["corp_code"]
            if corp and all_filings_known:
                numbers = sorted({item["rcept_no"] for item in issuers.get(corp, [])})
                row["issuer_receipts"] = numbers
                row["issuer_filing_count"] = len(numbers)
    if directory_error:
        summary["complete"] = False
    return summary, securities, issuers


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def display(value):
    return "미확인" if value is None else str(value)


def plain(value):
    return re.sub(r"([\\\[\]*_`<>|])", r"\\\1", " ".join(str(value).split()))


def write_files(folder, summary, securities, issuers):
    save_json(folder / "summary.json", summary)
    lines = ["# 한국 주식 일일 보고서", "", summary["source"], "", f"기준일 {summary['date']} · 생성 {summary['generated_at']}",
             "", "자료 수집 및 대조 " + ("완료" if summary["complete"] else "점검 필요"), "",
             "| 시장 | 장 상태 | 기본 종목 | 매매행 | 일치 | 매매 누락 | 매매만 | 커버리지 | 공시 | 정정 | 공시 발행사 |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for market, m in summary["markets"].items():
        coverage = m["coverage_pct"]
        fields = [market, m["session"], m["master"], m["trade"], m["matched"], m["missing_trade"],
                  m["trade_only"], "산정 불가" if coverage is None else f"{coverage:.2f}%",
                  m["filings"], m["corrections"], m["filing_issuers"]]
        lines.append("| " + " | ".join(map(display, fields)) + " |")
    lines.extend(["", "커버리지는 기준일이 맞는 매매행과 연결된 기본 종목의 비율입니다. "
                  "공시는 시장별 고유 접수번호로 셉니다. 발행사 연결 상태는 별도로 표시합니다.", "", "## 점검 내역", ""])
    for label, error in summary["errors"].items():
        lines.append(f"- {label} · {error}")
    for market, m in summary["markets"].items():
        lines.append(f"- {market} · 발행사 미연결 주권 {m['unlinked_securities']}개")
        if m["unmatched_filing_issuers"]:
            lines.append(f"  - 주권과 연결되지 않은 공시 발행사 {len(m['unmatched_filing_issuers'])}곳")
        if m["closed_evidence"]:
            lines.append("  - 휴장 근거 · " + plain(m["closed_evidence"]))
        if m["session"] == "미확인":
            lines.append("  - 해당 날짜의 매매행과 휴장 근거를 확인할 수 없습니다.")
        for label, error in m["errors"].items():
            lines.append(f"  - {label} · {error}")
    (folder / "issuers").mkdir()
    for corp, items in issuers.items():
        save_json(folder / "issuers" / (corp + ".json"), items)
    lines.extend(["", "## 접수한 공시", ""])
    for market, (_, cls) in MARKETS.items():
        lines.extend([f"### {market}", ""])
        docs = sorted((r for items in issuers.values() for r in items if r["corp_cls"] == cls),
                      key=lambda r: r["rcept_no"])
        if not docs:
            lines.append("공시 0건" if summary["markets"][market]["filings"] == 0 else "공시 건수 미확인")
        for row in docs:
            num = row["rcept_no"]
            lines.append(f"- {plain(row.get('corp_name', row['corp_code']))} · {plain(row['report_nm'])} "
                         f"· [원문 {num}](https://dart.fss.or.kr/dsaf001/main.do?rcpNo={num})")
        lines.append("")
    lines.extend(["## 종목별 결과", "", "종가 단위는 원, 거래량 단위는 주입니다. "
                  "공시 수는 해당 주권의 발행사가 접수 대상인 건수입니다.", ""])
    for market, rows in securities.items():
        dest = folder / "securities" / market
        dest.mkdir(parents=True)
        lines.extend([f"### {market}", "", "| 종목 | 코드 | 종가 | 거래량 | 발행사 공시 |",
                      "|---|---|---:|---:|---:|"])
        for code, row in rows.items():
            save_json(dest / (code + ".json"), row)
            trade = row["trade"] or {}
            name = (row["master"] or trade).get("ISU_NM", code)
            lines.append(f"| [{plain(name)}](securities/{market}/{code}.json) | {code} | "
                         f"{display(trade.get('TDD_CLSPRC'))} | {display(trade.get('ACC_TRDVOL'))} | "
                         f"{display(row['issuer_filing_count'])} |")
        lines.append("")
    (folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def publish(source, root, day, links, closed):
    dest = root / day
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (dest / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        run = "run-" + uuid4().hex
        staging = Path(tempfile.mkdtemp(dir=dest, prefix=".pending-"))
        try:
            result = build(source, day, links, closed)
            write_files(staging, *result)
            staging.rename(dest / run)
            for name in (["latest", "latest-complete"] if result[0]["complete"] else ["latest"]):
                pointer = dest / ("." + uuid4().hex)
                pointer.symlink_to(run)
                os.replace(pointer, dest / name)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return result[0]["complete"]


def main():
    parser = argparse.ArgumentParser(description="한국 주식 일일 보고서")
    yesterday = datetime.now(KST).date() - timedelta(days=1)
    parser.add_argument("--date", default=yesterday.strftime("%Y%m%d"), help="기준일 YYYYMMDD")
    parser.add_argument("--output", type=Path, default=Path("private-output"))
    parser.add_argument("--config", type=Path, help="비공개 휴장 근거 및 발행사 연결 JSON")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if day_value(args.date) > datetime.now(KST).date():
            raise ValueError("기준일은 한국 날짜 기준 오늘까지 가능합니다")
        links, closed = load_config(args.config, args.date)
    except (OSError, KeyError, TypeError, AttributeError, ValueError):
        parser.error("날짜 또는 비공개 설정의 형식·기간·근거를 확인하세요")
    complete = publish(Live(), args.output, args.date, links, closed)
    print("보고서 생성 완료" if complete else "점검 내역을 포함한 보고서 생성")
    return 0 if complete else 2


if __name__ == "__main__":
    sys.exit(main())
