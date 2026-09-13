"""Two bounded high-effort connectivity probes; never part of benchmark scoring."""
import json
import subprocess
import time
from pathlib import Path
import importlib.util

HERE = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location("glm", HERE.with_name("glm53-followup-20260909.py"))
glm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(glm)


def probe(stream, key):
    body = {**glm.SETTINGS, "stream": stream, "max_tokens": 2048,
            "messages": [{"role": "user", "content": '计算1到100的整数平方和。仅输出JSON对象，格式为{"sum":整数}。'}]}
    cfg = 'url = ' + json.dumps(glm.ENDPOINT) + '\nrequest = "POST"\nheader = ' + json.dumps('Authorization: Bearer ' + key)
    cfg += '\nheader = "Content-Type: application/json"\ndata = ' + json.dumps(json.dumps(body)) + '\n'
    start = time.monotonic()
    p = subprocess.Popen(['curl', '--config', '-', '--silent', '--show-error', '--no-buffer',
                          '--dump-header', '-', '--connect-timeout', '15', '--max-time', '90'],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    p.stdin.write(cfg); p.stdin.close()
    headers = True
    raw = []
    result = {"stream": stream, "max_tokens": 2048, "http_statuses": [], "reasoning_characters": 0,
              "answer_characters": 0, "sse_events": 0, "done": False, "usage": None}
    for line in p.stdout:
        if headers:
            if line.startswith('HTTP/'):
                result['http_statuses'].append(line.strip())
                print(json.dumps({"stream": stream, "http": line.strip(), "elapsed": round(time.monotonic()-start, 2)}), flush=True)
            elif line.lower().startswith('content-type:'):
                result['content_type'] = line.strip()
            elif line.strip() == '':
                headers = False
            continue
        raw.append(line)
        if line.startswith('data:'):
            s = line[5:].strip()
            if s == '[DONE]':
                result['done'] = True
                continue
            try: d = json.loads(s)
            except ValueError: continue
            result['sse_events'] += 1
            if result['sse_events'] == 1:
                result['first_sse_seconds'] = round(time.monotonic()-start, 2)
                print(json.dumps({"stream": stream, "first_sse_seconds": result['first_sse_seconds']}), flush=True)
            for choice in d.get('choices', []):
                delta = choice.get('delta', {})
                result['reasoning_characters'] += len(delta.get('reasoning_content') or '')
                result['answer_characters'] += len(delta.get('content') or '')
                if choice.get('finish_reason'): result['finish_reason'] = choice['finish_reason']
            if d.get('usage'): result['usage'] = d['usage']
    p.wait()
    result['curl_exit'] = p.returncode
    result['elapsed_seconds'] = round(time.monotonic()-start, 2)
    result['curl_error'] = p.stderr.read().replace(key, '[REDACTED]')[:500]
    if not stream or not result['sse_events']:
        try:
            d = json.loads(''.join(raw))
            result['returned_model'] = d.get('model')
            if 'error' in d: result['api_error'] = str(d['error']).replace(key, '[REDACTED]')[:500]
            result['usage'] = d.get('usage')
            for choice in d.get('choices', []):
                msg = choice.get('message', {})
                result['reasoning_characters'] += len(msg.get('reasoning_content') or '')
                result['answer_characters'] += len(msg.get('content') or '')
                result['finish_reason'] = choice.get('finish_reason')
        except ValueError:
            result['non_json_body_bytes'] = len(''.join(raw).encode())
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


if __name__ == '__main__':
    key = glm.read_key()
    results = [probe(stream, key) for stream in (True, False)]
    glm.base.save(glm.OUT / 'connectivity-diagnostic.json', {
        'kind': 'separate_connectivity_diagnostic', 'benchmark_evidence': False,
        'new_calls': 2, 'maximum_completion_tokens': 4096, 'results': results})
