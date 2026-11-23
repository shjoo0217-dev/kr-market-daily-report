"""Read complete OpenDART issuer listings only for unmatched B signal date/market pairs."""
import csv
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('OUT_DIR', 'artifacts'))/'control'
ROOT.mkdir(parents=True,exist_ok=True)
KEY=os.environ.get('OPENDART_API_KEY','').strip()
KST=dt.timezone(dt.timedelta(hours=9))
END=dt.date(2026,11,23)
START=dt.date(2025,11,24)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def stamp():
    return dt.datetime.now(KST).isoformat(timespec='seconds')

def safe_json(obj):
    text=json.dumps(obj,ensure_ascii=False)
    if KEY and KEY in text:
        raise ValueError('credential_guard')
    return text

def line(name,obj):
    with (ROOT/name).open('a',encoding='utf-8') as stream:
        stream.write(safe_json(obj)+'\n')

class Run:
    def __init__(self):
        self.requests=0
        self.completed=0
        self.rows=0
        self.reason=''
        self.start=time.monotonic()
        self.http=urllib.request.build_opener(NoRedirect())

    def get(self,params):
        rec=dict(endpoint='list.json',params=params,requested_kst=stamp())
        try:
            if self.requests>=1800 or time.monotonic()-self.start>1490:
                self.reason='resource_budget'
                rec['status']=self.reason
                return
            self.requests+=1
            query=urllib.parse.urlencode({**params,'crtfc_key':KEY})
            with self.http.open('https://opendart.fss.or.kr/api/list.json?'+query,timeout=18) as response:
                raw=response.read(2_000_001)
            if len(raw)>2_000_000 or KEY.encode() in raw:
                raise ValueError('size_or_credential_guard')
            result=json.loads(raw)
            status=result.get('status')
            if not isinstance(status,str) or not re.fullmatch(r'\d{3}',status):
                raise ValueError('invalid_service_status')
            rec['status']=status
            if status in ('010','011','012','020','901'):
                self.reason='provider_'+status
            if status not in ('000','013'):
                return
            digest=hashlib.sha256(raw).hexdigest()
            folder=ROOT/'raw';folder.mkdir(exist_ok=True)
            file=folder/(digest+'.json.gz')
            if not file.exists():
                file.write_bytes(gzip.compress(raw,mtime=0))
            rec.update(sha256=digest,path=str(file.relative_to(ROOT)),bytes=len(raw))
            return result
        except Exception as exc:
            rec['status']='failed_'+type(exc).__name__
            if isinstance(exc,urllib.error.HTTPError):
                rec['http_status']=exc.code
                if exc.code in (401,403,429):self.reason='provider_http_'+str(exc.code)
        finally:
            rec['received_kst']=stamp()
            line('requests.jsonl',rec)
            time.sleep(.22)

    def group(self,day,market):
        ymd=day.replace('-','')
        records=[]
        pages=None
        expected=None
        all_records={}
        pg=1
        reason=''
        while not self.reason:
            params=dict(bgn_de=ymd,end_de=ymd,corp_cls=market,page_count='100',page_no=str(pg),last_reprt_at='N')
            obj=self.get(params)
            if not obj:
                reason='provider_failure'
                break
            if obj.get('status')=='013' and pg==1:
                pages=0;expected=0
                break
            if obj.get('status')!='000':
                reason='unexpected_page_status'
                break
            current_pages=int(obj['total_page'])
            current_count=int(obj['total_count'])
            if pages is None:pages,expected=current_pages,current_count
            if pages!=current_pages or expected!=current_count or int(obj['page_no'])!=pg:
                reason='page_count_changed'
                break
            for row in obj.get('list',[]):
                no=row.get('rcept_no','')
                if (row.get('corp_cls')!=market or row.get('rcept_dt')!=ymd or not re.fullmatch(r'\d{14}',no)
                        or no in all_records):
                    reason='duplicate_receipt_or_wrong_date_market'
                    break
                all_records[no]=row
            if reason:break
            if pg>=pages:break
            pg+=1
        complete=not self.reason and not reason and expected==len(all_records)
        line('groups.jsonl',dict(date=day,market=market,expected_pages=pages,completed_pages=min(pg,pages) if pages is not None else 0,
                                 expected_rows=expected,rows=len(all_records),complete=complete,reason=self.reason or reason,
                                 verified_kst=stamp()))
        if complete:
            self.completed+=1
            self.rows+=len(all_records)
            for row in all_records.values():
                line('receipts.jsonl',row)

    def run(self):
        if not KEY:
            self.reason='missing_OPENDART_API_KEY'
            return
        rows=list(csv.DictReader((HERE/'control_signal_days.csv').open(encoding='utf-8-sig')))
        groups=sorted({(x['date'],x['market']) for x in rows})
        if any(g not in ('Y','K') or not START<=dt.date.fromisoformat(d)<=END for d,g in groups):
            self.reason='invalid_input'
            return
        for day,market in groups:
            if self.reason:break
            self.group(day,market)

if __name__=='__main__':
    job=Run()
    try:job.run()
    except Exception as exc:job.reason='internal_'+type(exc).__name__
    (ROOT/'status.json').write_text(safe_json(dict(status=job.reason or 'planned_groups_processed',
                                    completed_groups=job.completed,requests=job.requests,verified_rows=job.rows,
                                    finished_kst=stamp())),encoding='utf-8')
    print('Control coverage finished. Refer to the encrypted group evidence.')
