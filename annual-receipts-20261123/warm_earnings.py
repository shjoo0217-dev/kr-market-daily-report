"""Collect Y/K warm-up exchange and periodic lists plus eligible preliminary receipt bodies."""
import hashlib,io,json,os,re,time,urllib.parse,urllib.request,zipfile
from pathlib import Path
from datetime import datetime,timedelta,timezone
from xml.etree import ElementTree as ET
ROOT=Path(os.environ.get('OUT_DIR','out'))/'warm'
ROOT.mkdir(exist_ok=True,parents=True)
RAW=ROOT/'raw';RAW.mkdir(exist_ok=True)
KEY=os.environ.get('OPENDART_API_KEY','').strip().encode()
KST=timezone(timedelta(hours=9))
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None
http=urllib.request.build_opener(NoRedirect())
def now():return datetime.now(KST).isoformat(timespec='seconds')
def log(file,row):
    line=json.dumps(row,ensure_ascii=False)
    if KEY and KEY in line.encode():raise RuntimeError('guard')
    with (ROOT/file).open('a',encoding='utf8') as f:f.write(line+'\n')
class Collector:
    def __init__(self):self.requests=0;self.stop='';self.rcps={};self.bodies=0
    def fetch(self,endpoint,params):
        if self.stop:return
        self.requests+=1
        if self.requests>400:self.stop='call_budget';return
        q=urllib.parse.urlencode({**params,'crtfc_key':KEY.decode()}); rec=dict(endpoint=endpoint,params=params,started_kst=now())
        try:
            with http.open('https://opendart.fss.or.kr/api/'+endpoint+'?'+q,timeout=15) as res:body=res.read(15*1024**2+1)
            if len(body)>15*1024**2 or KEY in body:raise ValueError('guard_or_size')
            obj=None
            if endpoint=='list.json':
                obj=json.loads(body);status=str(obj.get('status','invalid'));ext='.json'
            elif zipfile.is_zipfile(io.BytesIO(body)):
                with zipfile.ZipFile(io.BytesIO(body)) as z:
                    if sum(a.file_size for a in z.infolist())>50*1024**2:raise ValueError('size')
                    for name in z.namelist():
                        if KEY in z.read(name):raise ValueError('guard')
                    if params['rcept_no']+'.xml' not in z.namelist():raise ValueError('not_receipt_file')
                status='000';ext='.zip'
            else:
                status=ET.fromstring(body).findtext('status') or 'invalid';ext='.xml'
            if not re.fullmatch(r'\d{3}',status):status='invalid'
            rec['status']=status
            if status in ('010','011','012','020','901'):self.stop='provider_'+status
            if status=='000':
                sha=hashlib.sha256(body).hexdigest();fp=RAW/(sha+ext)
                if not fp.exists():fp.write_bytes(body)
                rec.update(sha256=sha,path='raw/'+fp.name,bytes=len(body))
                if ext=='.zip':self.bodies+=1
                return obj
        except Exception as exc:
            rec['status']=type(exc).__name__
            if hasattr(exc,'code'):
                rec['http_status']=exc.code
                if exc.code in (401,403,429):self.stop='provider_http_'+str(exc.code)
        finally:
            rec['received_kst']=now();log('requests.jsonl',rec);time.sleep(.15)
    def run(self):
        if not KEY:self.stop='missing_key';return
        for cat in ('I','A'):
            for m in ('Y','K'):
                pg=1
                while not self.stop:
                    params=dict(bgn_de='20251114',end_de='20251123',corp_cls=m,pblntf_ty=cat,last_reprt_at='N',page_count='100',page_no=str(pg))
                    response=self.fetch('list.json',params)
                    if not response:break
                    for r in response.get('list',[]):
                        no=r.get('rcept_no','')
                        if not re.fullmatch(r'\d{14}',no) or r.get('corp_cls')!=m:continue
                        if no in self.rcps:continue
                        self.rcps[no]=r;log('receipts.jsonl',dict(r,query_type=cat))
                    if pg>=int(response.get('total_page',0)):break
                    pg+=1
        for no,r in sorted(self.rcps.items()):
            title=re.sub(r'\s+','',r.get('report_nm',''))
            if '영업(잠정)실적' in title or ('잠정' in title and '실적' in title):
                log('preliminary_index.jsonl',r);self.fetch('document.xml',dict(rcept_no=no))
if __name__=='__main__':
    obj=Collector()
    try:obj.run()
    except Exception as exc:obj.stop='internal_'+type(exc).__name__
    log('status.jsonl',dict(status=obj.stop or 'finished',all_receipts=len(obj.rcps),requests=obj.requests,receipt_bodies=obj.bodies,at=now()))
    print("Warm-up acquisition finished. Read the encrypted artifact for coverage.")
