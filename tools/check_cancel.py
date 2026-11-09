"""Board regression: cancel a live Qwen stream, then complete another request."""
import json
from pathlib import Path
import time
from urllib.request import Request, ProxyHandler, build_opener

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8080'
OPENER=build_opener(ProxyHandler({}))


def request(path, value=None, timeout=120):
    req=Request(BASE+path, data=None if value is None else json.dumps(value,ensure_ascii=False).encode(),
                headers={'Content-Type':'application/json'})
    return OPENER.open(req,timeout=timeout)


def main():
    payload={'model':'qwen2-0.5b','messages':[{'role':'user','content':'请从1依次写到1000，每个数字之间用逗号分隔。'}],'stream':True}
    started=time.monotonic()
    with request('/api/chat',payload) as response:
        token=response.headers.get('X-Request-ID')
        first=json.loads(response.readline(1024*1024))
        if not token or first.get('done') is True or first.get('error'):
            raise RuntimeError('Did not observe a live inference request')
        with request('/api/cancel',{'request_id':token}) as reply:
            cancelled=json.load(reply)
        if cancelled.get('cancelled') is not True:
            raise RuntimeError('Request finished before cancellation; repeat this test')
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            with request('/healthz') as reply: health=json.load(reply)
            if not health['busy']:break
            time.sleep(.1)
        else:raise RuntimeError('Backend is still busy after cancellation')
    with request('/api/chat',{'model':'qwen2-0.5b','messages':[{'role':'user','content':'二加三等于几？请简短回答。'}],'stream':False}) as reply:
        answer=json.load(reply)
    if answer.get('done') is not True or not answer.get('message',{}).get('content','').strip():
        raise RuntimeError('Next request failed after cancellation')
    result={'cancelled':True,'recovered':True,'seconds':time.monotonic()-started,'next_reply':answer['message']['content']}
    folder=ROOT/'integration-artifacts';folder.mkdir(exist_ok=True)
    (folder/'cancel-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('CANCEL_AND_NEXT_REQUEST_OK',json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
