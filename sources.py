"""KRX and OpenDART adapters. Errors contain only controlled diagnostic text."""
import io
import json
import os
import re
import time
import zipfile
from datetime import date
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree as ET

MARKETS = {"KOSPI": ("stk", "Y"), "KOSDAQ": ("ksq", "K"), "KONEX": ("knx", "N")}
KRX = "https://data-dbg.krx.co.kr/svc/apis/sto/"
DART = "https://opendart.fss.or.kr/api/"
CODE = re.compile(r"[0-9A-Z]{6}")
CORP = re.compile(r"[0-9]{8}")


class SourceError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise SourceError(message)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get(url, params, headers):
    request = Request(url + "?" + urlencode(params), headers=headers)
    opener = build_opener(NoRedirect())
    for attempt in range(3):
        try:
            with opener.open(request, timeout=35) as response:
                data = response.read(64 * 1024 * 1024 + 1)
                require(len(data) <= 64 * 1024 * 1024, "응답 크기 초과")
                return data
        except HTTPError as exc:
            reason = "HTTP %d" % exc.code
            if exc.code not in (429, 500, 502, 503, 504):
                raise SourceError(reason) from None
        except (URLError, OSError, HTTPException):
            reason = "네트워크 또는 응답 읽기 오류"
        if attempt < 2:
            time.sleep(2 ** attempt)
    raise SourceError(reason)


def parse_json(data):
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeError):
        raise SourceError("JSON 해석 실패") from None
    require(isinstance(payload, dict), "응답 객체 누락")
    return payload


def day_value(raw):
    if not isinstance(raw, str) or not re.fullmatch(r"[0-9]{8}", raw):
        raise ValueError("날짜는 YYYYMMDD 형식입니다")
    return date.fromisoformat(raw[:4] + "-" + raw[4:6] + "-" + raw[6:])


def krx_rows(payload, market, kind, day):
    rows = payload.get("OutBlock_1")
    require(isinstance(rows, list), "OutBlock_1 누락")
    indexed = {}
    for row in rows:
        require(isinstance(row, dict), "행 형식 오류")
        master = kind == "master"
        code = row.get("ISU_SRT_CD" if master else "ISU_CD")
        require(isinstance(code, str) and CODE.fullmatch(code), "종목코드 오류")
        require(code not in indexed, "종목코드 중복")
        market_name = row.get("MKT_TP_NM" if master else "MKT_NM")
        allowed = {market, "KOSDAQ GLOBAL"} if market == "KOSDAQ" else {market}
        require(market_name in allowed, "응답 시장 불일치")
        if not master or "BAS_DD" in row:
            require(row.get("BAS_DD") == day, "응답 기준일 불일치")
        if master:
            try:
                listed = day_value(row.get("LIST_DD"))
            except ValueError:
                raise SourceError("상장일 형식 오류") from None
            require(listed <= day_value(day), "기준일 이후 상장")
            require(isinstance(row.get("ISU_CD"), str) and len(row["ISU_CD"]) == 12,
                    "표준코드 오류")
        else:
            require(all(key in row for key in ("TDD_CLSPRC", "ACC_TRDVOL", "ACC_TRDVAL")),
                    "매매 필수 필드 누락")
        indexed[code] = row
    return indexed


def status(payload):
    value = payload.get("status")
    return value if isinstance(value, str) and re.fullmatch(r"[0-9]{3}", value) else "형식 오류"


def filings(fetch, day, cls):
    result = {}
    page = 1
    expected = None
    while True:
        payload = fetch({"bgn_de": day, "end_de": day, "corp_cls": cls,
                         "last_reprt_at": "N", "page_no": page, "page_count": 100})
        if status(payload) == "013" and page == 1:
            return result
        require(status(payload) == "000", "DART " + status(payload))
        try:
            actual, size, total, pages = (int(payload[k]) for k in
                                         ("page_no", "page_count", "total_count", "total_page"))
        except (KeyError, ValueError, TypeError):
            raise SourceError("페이지 정보 오류") from None
        require(actual == page and size == 100 and total >= 0 and pages == (total + 99) // 100
                and expected in (None, total), "페이지 합계 불일치")
        expected = total
        rows = payload.get("list")
        require(isinstance(rows, list) and len(rows) == min(100, total - len(result)),
                "공시 페이지 누락")
        for row in rows:
            require(isinstance(row, dict), "공시 행 오류")
            receipt = row.get("rcept_no", "")
            require(isinstance(receipt, str) and re.fullmatch(r"[0-9]{14}", receipt)
                    and receipt not in result, "접수번호 오류 또는 중복")
            require(row.get("rcept_dt") == day and row.get("corp_cls") == cls, "공시 접수일 또는 시장 불일치")
            require(isinstance(row.get("corp_code"), str) and CORP.fullmatch(row["corp_code"])
                    and isinstance(row.get("report_nm"), str) and row["report_nm"].strip(),
                    "공시 발행사 또는 제목 오류")
            result[receipt] = row
        if page >= pages:
            return result
        page += 1


def directory(data):
    if not zipfile.is_zipfile(io.BytesIO(data)):
        try:
            msg = ET.fromstring(data).findtext("status")
        except ET.ParseError:
            msg = None
        raise SourceError("고유번호 " + status({"status": msg}))
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("CORPCODE.xml")
            require(info.file_size <= 160 * 1024 * 1024, "고유번호 파일 크기 초과")
            entries = {}
            with archive.open(info) as stream:
                for _, element in ET.iterparse(stream, events=("end",)):
                    if element.tag != "list":
                        continue
                    code = (element.findtext("stock_code") or "").strip()
                    if code:
                        corp = element.findtext("corp_code") or ""
                        modified = element.findtext("modify_date") or ""
                        require(CODE.fullmatch(code) and CORP.fullmatch(corp), "고유번호 코드 오류")
                        day_value(modified)
                        entries.setdefault(code, []).append((corp, modified))
                    element.clear()
            require(entries, "상장사 고유번호 목록 비어 있음")
            return entries
    except (ET.ParseError, ValueError, KeyError, zipfile.BadZipFile, EOFError, RuntimeError):
        raise SourceError("고유번호 ZIP/XML 오류") from None


class Live:
    def __init__(self):
        self.krx_key = os.getenv("KRX_AUTH_KEY") or os.getenv("KRX_API_KEY")
        self.dart_key = os.getenv("OPENDART_API_KEY") or os.getenv("DART_API_KEY")

    def krx(self, market, kind, day):
        require(self.krx_key, "KRX 키 미설정")
        suffix = "isu_base_info" if kind == "master" else "bydd_trd"
        url = KRX + MARKETS[market][0] + "_" + suffix
        return parse_json(get(url, {"basDd": day}, {"AUTH_KEY": self.krx_key}))

    def dart(self, path, params):
        require(self.dart_key, "OpenDART 키 미설정")
        return get(DART + path, dict(params, crtfc_key=self.dart_key), {})

    def page(self, params):
        return parse_json(self.dart("list.json", params))

    def companies(self):
        return directory(self.dart("corpCode.xml", {}))
