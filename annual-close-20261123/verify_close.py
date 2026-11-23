"""Verify the fixed final date again after the first official source published empty responses."""
import csv,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'annual-research-20261123'))
import collect_krx
out=Path(os.environ['OUT_DIR'])/'close';collect_krx.ROOT=out;out.mkdir(parents=True,exist_ok=True)
rows=[collect_krx.fetch(service,collect_krx.END,os.environ['KRX_AUTH_KEY']) for service in collect_krx.SERVICES if service!='kosdaq_dd_trd']
with (out/'requests.csv').open('w') as f:
    w=csv.DictWriter(f,['service','date','url','requested_kst','received_kst','status','attempts','rows','sha256','file']);w.writeheader();w.writerows(rows)
(out/'status.json').write_text(json.dumps({r['service']:r['status'] for r in rows}))
