"""Fetch only KRX public data records with a service-scoped KRX authentication header."""
import collections
import concurrent.futures
import csv
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request

ROOT = Path(os.environ.get("OUT_DIR", "out")) / "krx"
SERVICES = {
    "stk_bydd_trd": ("sto", "ISU_CD"),
    "ksq_bydd_trd": ("sto", "ISU_CD"),
    "stk_isu_base_info": ("sto", "ISU_SRT_CD"),
    "ksq_isu_base_info": ("sto", "ISU_SRT_CD"),
    "kospi_dd_trd": ("idx", "IDX_NM"),
    "kosdaq_dd_trd": ("idx", "IDX_NM"),
}
END = "20261123"
KST = dt.timezone(dt.timedelta(hours=9))
LOCK = threading.Lock()
NEXT_REQUEST = 0.0

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None

def now():
    return dt.datetime.now(KST).isoformat(timespec="seconds")

def load_dates():
    candidates = [Path(__file__).with_name("calendar.csv"), Path("market/normalized/calendar.csv"),Path("calendar.csv")]
    for p in candidates:
        if p.is_file():
            with p.open() as f:
                days = sorted({r["date"].replace("-","") for r in csv.DictReader(f)})
            assert days[0]=="20250801" and days[-1]==END and len(days)==319
            return days
    dates = []
    day = dt.date(2025,8,1)
    while day <= dt.date(2026,11,23):
        if day.weekday()<5:
            dates.append(day.strftime("%Y%m%d"))
        day += dt.timedelta(days=1)
    return dates

def wait_slot():
    global NEXT_REQUEST
    with LOCK:
        delay = max(0.0, NEXT_REQUEST-time.monotonic())
        NEXT_REQUEST = time.monotonic() + delay + 0.16
    time.sleep(delay)

def fetch(service, day, key):
    group, idfield = SERVICES[service]
    url = f"https://data-dbg.krx.co.kr/svc/apis/{group}/{service}.json?basDd={day}"
    row = {"service":service,"date":day,"url":url,"requested_kst":now(),"rows":0}
    for attempt in range(1,4):
        wait_slot()
        row["attempts"] = attempt
        try:
            request = urllib.request.Request(url,headers={"AUTH_KEY":key,"Accept":"application/json"})
            with urllib.request.build_opener(NoRedirect()).open(request,timeout=16) as response:
                data = response.read(8_000_001)
            if len(data)>8_000_000 or key.encode() in data:
                row["status"]="invalid_response"
                break
            decoded = json.loads(data)
            records = decoded.get("OutBlock_1")
            if not isinstance(records,list):
                row["status"]="api_no_data_field"
                break
            if any(not isinstance(r,dict) or not r.get(idfield) or r.get("BAS_DD",day)!=day for r in records):
                row["status"]="record_schema_or_date_error"
                break
            ids=[r[idfield] for r in records]
            if len(ids)!=len(set(ids)):
                row["status"]="duplicate_ids"
                break
            if not records:
                row["status"]="empty_at_receipt"
                break
            folder=ROOT/service
            folder.mkdir(parents=True,exist_ok=True)
            dest=folder/(day+".json.gz")
            with dest.open("wb") as f:
                f.write(gzip.compress(data,compresslevel=4,mtime=0))
            row.update(status="ok",rows=len(records),sha256=hashlib.sha256(data).hexdigest(),file=f"{service}/{dest.name}")
            break
        except urllib.error.HTTPError as exc:
            row["status"]="http_"+str(exc.code)
            if exc.code not in (429,500,502,503,504):
                break
            time.sleep(2**attempt)
        except (OSError,ValueError,TimeoutError,AttributeError):
            row["status"]="transport_or_parse_error"
            time.sleep(2**attempt)
    row["received_kst"]=now()
    return row

def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    key=os.environ.get("KRX_AUTH_KEY","").strip()
    if not key:
        (ROOT/"status.json").write_text(json.dumps({"status":"credential_unavailable"}))
        return
    days=load_dates()
    pilot_days=[END,"20261120"]
    fields=["service","date","url","requested_kst","received_kst","status","attempts","rows","sha256","file"]
    summary=collections.Counter()
    with (ROOT/"requests.csv").open("w",newline="") as logfile, concurrent.futures.ThreadPoolExecutor(5) as ex:
        writer=csv.DictWriter(logfile,fieldnames=fields)
        writer.writeheader()
        def dispatch(tasks):
            replies=[]
            for r in ex.map(lambda t:fetch(t[0],t[1],key),tasks):
                writer.writerow(r)
                summary[(r["service"],r["status"])]+=1
                replies.append(r)
            logfile.flush()
            return replies
        pilots=dispatch([(s,d) for d in pilot_days for s in SERVICES])
        usable={r["service"] for r in pilots if r["status"]=="ok"}
        for k in range(len(days)-1,-1,-10):
            selected=[d for d in days[max(0,k-9):k+1] if d not in pilot_days]
            dispatch([(s,d) for d in reversed(selected) for s in SERVICES if s in usable])
    report={"target":END,"dates_planned":len(days),"successful_pilot_services":sorted(usable),
            "by_service_status":{"|".join(k):n for k,n in sorted(summary.items())}}
    (ROOT/"status.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False))

if __name__=="__main__":
    main()
