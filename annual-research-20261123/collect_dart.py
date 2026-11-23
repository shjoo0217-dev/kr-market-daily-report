"""Read-only OpenDART acquisition. Standard library only. No credentials in artifacts."""
import calendar
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import time
from datetime import date, datetime, timedelta, timezone
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

END = date(2026, 11, 23)
START = date(2024, 1, 1)
KST = timezone(timedelta(hours=9))
ROOT = Path(os.environ.get("OUT_DIR", "artifacts")) / "dart"
KEY = os.environ.get("OPENDART_API_KEY", "").strip()
PILOTS = ["005930", "000270", "015760", "067160", "000100"]
LIMIT_REQUESTS = 1200
LIMIT_STORAGE = 500 * 1024**2
LIMIT_BODY = 40 * 1024**2
LIMIT_SECONDS = int(os.environ.get("DART_TIME_BUDGET", "480"))

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def now():
    return datetime.now(KST).isoformat(timespec="seconds")

def clean(body):
    return not KEY or KEY.encode() not in body

def write_line(name, content):
    line = json.dumps(content, ensure_ascii=False, sort_keys=True)
    if not clean(line.encode()):
        raise RuntimeError("credential guard")
    with (ROOT/name).open("a", encoding="utf8") as f:
        f.write(line+"\n")

class Collector:
    def __init__(self):
        self.http = urllib.request.build_opener(NoRedirect())
        self.start = time.monotonic()
        self.requests = self.bytes = 0
        self.stop = ""
        self.receipts = {}

    def fetch(self, endpoint, params):
        if self.requests >= LIMIT_REQUESTS or self.bytes >= LIMIT_STORAGE or time.monotonic()-self.start >= LIMIT_SECONDS:
            self.stop = self.stop or "bounded_resource_budget"
        if self.stop:
            return None
        self.requests += 1
        query = {"crtfc_key": KEY, **params}
        url = "https://opendart.fss.or.kr/api/"+endpoint+"?"+urllib.parse.urlencode(query)
        log = dict(start=now(), endpoint=endpoint, params=params)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "market-research-collection/1.0"})
            with self.http.open(req,timeout=25) as res:
                body = res.read(LIMIT_BODY+1)
            if len(body)>LIMIT_BODY or not clean(body):
                log["status"]="excluded_size_or_guard"
                return None
            parsed = None
            if zipfile.is_zipfile(io.BytesIO(body)):
                with zipfile.ZipFile(io.BytesIO(body)) as z:
                    if sum(v.file_size for v in z.infolist()) > 180*1024**2:
                        log["status"]="oversized_uncompressed"
                        return None
                    # Check decompressed content as well as the container before saving it.
                    for name in z.namelist():
                        if not clean(z.read(name)):
                            log["status"]="excluded_guard"
                            return None
                suffix, status = ".zip", "000"
            elif endpoint.endswith(".json"):
                parsed = json.loads(body)
                suffix, status = ".json", str(parsed["status"])
            else:
                root=ET.fromstring(body)
                suffix, status = ".xml", (root.findtext("status") or "invalid_xml")
            log["status"]=status
            if status in ("010","011","012","020","021","100","101","901"):
                if status in ("010","011","012","020","901"):
                    self.stop="provider_"+status
                return None
            if status not in ("000","013"):
                return None
            sha=hashlib.sha256(body).hexdigest()
            folder=ROOT/"raw"/endpoint.split(".")[0]
            folder.mkdir(parents=True,exist_ok=True)
            target=folder/(sha+suffix)
            if not target.exists():
                target.write_bytes(body)
                self.bytes+=len(body)
            log.update(sha256=sha,path=str(target.relative_to(ROOT)),bytes=len(body))
            if parsed is not None:
                return parsed
            return body if status=="000" and suffix==".zip" else None
        except Exception as exc:
            log["status"]="failed_"+type(exc).__name__
            if hasattr(exc,"code"):
                log["http_status"]=exc.code
        finally:
            log["end"]=now()
            write_line("requests.jsonl",log)
        return None

    def listings(self, params):
        page=1
        while not self.stop:
            obj=self.fetch("list.json",{**params,"page_count":"100","page_no":str(page),"last_reprt_at":"N"})
            if not isinstance(obj,dict) or obj.get("status")!="000":
                return
            for item in obj.get("list",[]):
                if item.get("corp_cls") not in ("Y","K"): continue
                key=item.get("rcept_no","")
                if not re.fullmatch(r"\d{14}",key): continue
                if key not in self.receipts:
                    self.receipts[key]=item
                    write_line("receipts.jsonl",item)
            if page>=int(obj.get("total_page",0)): return
            page+=1

    def docs(self, corp_order):
        reports=[]
        for no,row in self.receipts.items():
            if row["corp_code"] not in corp_order: continue
            title=row["report_nm"]
            match=re.search(r"(사업|반기|분기)보고서\s*\(\s*(20\d\d)\.(\d\d)",title)
            if not match:continue
            month=int(match[3])
            code={3:"11013",6:"11012",9:"11014",12:"11011"}.get(month)
            if code:
                reports.append((row["rcept_dt"],corp_order.index(row["corp_code"]),no,code))
        # Cover most recent filings first, retaining originals as well as corrections.
        ordered=sorted(reports,key=lambda v:(v[1]!=0,-int(v[0]),v[1],v[2]))
        for _,_,no,code in ordered:
            if self.stop:break
            self.fetch("document.xml",{"rcept_no":no})
            self.fetch("fnlttXbrl.xml",{"rcept_no":no,"reprt_code":code})

    def run(self):
        if not KEY:
            self.stop="missing_OPENDART_API_KEY"
            return
        body=self.fetch("corpCode.xml",{})
        corp_order=[]
        if isinstance(body,bytes):
            with zipfile.ZipFile(io.BytesIO(body)) as z:
                name=next(n for n in z.namelist() if n.upper().endswith(".XML"))
                rows=ET.fromstring(z.read(name))
                lookup={r.findtext("stock_code","").strip():r.findtext("corp_code","").strip() for r in rows.findall("list")}
                corp_order=[lookup[x] for x in PILOTS if x in lookup]
        for corp in corp_order:
            for ty in ("A","I"):
                self.listings(dict(corp_code=corp,bgn_de="20240101",end_de="20261123",pblntf_ty=ty))
        self.docs(corp_order)
        months=[]
        for year in range(START.year,END.year+1):
            for month in range(1,13):
                first=date(year,month,1)
                if first>END:break
                last=min(END,date(year,month,calendar.monthrange(year,month)[1]))
                months.append((first.strftime("%Y%m%d"),last.strftime("%Y%m%d")))
        # Market-wide filing index, present to past. A and I have separate pagination.
        for a,b in reversed(months):
            for ty in ("A","I"):
                if self.stop:return
                self.listings(dict(bgn_de=a,end_de=b,pblntf_ty=ty))
        # Use remaining budget to obtain each pilot's preliminary earnings receipt bodies.
        for row in sorted(self.receipts.values(),key=lambda r:r["rcept_no"],reverse=True):
            if self.stop:return
            if row.get("corp_code") in corp_order and ("잠정" in row["report_nm"] or "영업실적" in row["report_nm"]):
                self.fetch("document.xml",{"rcept_no":row["rcept_no"]})

if __name__=="__main__":
    ROOT.mkdir(parents=True,exist_ok=True)
    collector=Collector()
    try:
        collector.run()
    except Exception as exc:
        collector.stop="internal_"+type(exc).__name__
    status=dict(at=now(),requests=collector.requests,stored_bytes=collector.bytes,unique_receipts=len(collector.receipts),
                status=collector.stop or "planned_scope_complete",pilot_codes=PILOTS,start=str(START),end=str(END))
    write_line("status.jsonl",status)
    print("OpenDART collection finished. Status and per-request coverage are in the artifact.")
