"""Replay archived cases over HTTP without retrying deterministic failures.

Run against an isolated staging server. Full responses are saved as gzip JSONL;
the small index keeps UUIDs, outcomes, diagnostic counts, and example messages.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--mode', choices=('check-errors', 'verify-all'), required=True)
    parser.add_argument('--concurrency', type=int, default=8)
    parser.add_argument('--timeout-seconds', type=float, default=120)
    parser.add_argument('--uuid', action='append', help='Replay only these UUIDs (repeatable).')
    parser.add_argument('--exclude-formal-errors', action='store_true',
                        help='Skip archived AXLE formal_statement compilation failures.')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--archive', type=Path, default=Path(__file__).parent/'data/disagreements.jsonl')
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error('concurrency must be positive')
    maximum = 120 if args.mode == 'check-errors' else 600
    if not 0 < args.timeout_seconds <= maximum:
        parser.error(f'timeout-seconds must be positive and at most {maximum}')
    rows = [json.loads(line) for line in args.archive.open()]
    excluded = []
    if args.exclude_formal_errors:
        excluded = [row['uuid'] for row in rows if any(
            error.startswith('failed to compile formal_statement:')
            for error in row['axle'].get('lean_errors', []))]
        excluded_set = set(excluded)
        rows = [row for row in rows if row['uuid'] not in excluded_set]
    if args.mode == 'check-errors':
        rows = [row for row in rows if row['local']['status'] == 'error']
    if args.uuid:
        selected = set(args.uuid)
        rows = [row for row in rows if row['uuid'] in selected]
        missing = selected - {row['uuid'] for row in rows}
        if missing:
            parser.error(f'UUIDs absent from selected scope: {sorted(missing)}')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    base = args.output_dir / args.mode
    started = time.monotonic()
    with args.archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    metadata = {'started_at': datetime.now(timezone.utc).isoformat(), 'base_url': args.base_url,
                'mode': args.mode, 'count': len(rows), 'archive_sha256': digest,
                'excluded_formal_error_count': len(excluded),
                'concurrency': args.concurrency, 'timeout_seconds': args.timeout_seconds,
                'selected_uuids': args.uuid, 'retries': 0}
    Path(str(base)+'-metadata.json').write_text(json.dumps(metadata, indent=2)+'\n')

    def request(row):
        if args.mode == 'check-errors':
            path = '/api/v1/check'
            payload = {'code': row['candidate'], 'allow_sorry': False, 'timeout_seconds': args.timeout_seconds}
        else:
            path = '/api/v1/verify_proof'
            payload = {'formal_statement': row['formal_statement'], 'content': row['candidate'],
                       'environment': 'lean-4.30.0', 'timeout_seconds': args.timeout_seconds, 'use_def_eq': True}
        req = Request(args.base_url.rstrip('/')+path, data=json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json'})
        began = time.monotonic()
        status = None
        try:
            opener = build_opener(ProxyHandler({}))
            try:
                response = opener.open(req, timeout=args.timeout_seconds+15)
            except HTTPError as exc:
                response = exc
            with response:
                status = response.code
                raw = response.read()
            body = json.loads(raw)
            if status == 200 and body.get('okay') is True:
                outcome = 'passed'
            elif body.get('timed_out') or body.get('error_type') == 'LeanTimeout':
                outcome = 'timeout'
            elif status == 200 and body.get('okay') is False:
                outcome = 'rejected'
            else:
                outcome = 'infrastructure_error'
        except Exception as exc:
            outcome = 'transport_error'
            body = {'error_type': type(exc).__name__, 'error': str(exc)}
            raw = b''
        return {'uuid': row['uuid'], 'line': row['line'], 'category': row['category'],
                'axle_status': row['axle']['status'], 'outcome': outcome, 'http_status': status,
                'response_bytes': len(raw), 'elapsed_seconds': round(time.monotonic()-began, 3),
                'body': body}

    results = []
    with gzip.open(str(base)+'-responses.jsonl.gz', 'wt', encoding='utf-8') as full, \
            Path(str(base)+'-index.jsonl').open('w') as index, \
            ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(request, row) for row in rows]
        for future in as_completed(futures):
            item = future.result()
            full.write(json.dumps(item, ensure_ascii=False)+'\n')
            full.flush()
            body = item['body']
            errors = body.get('errors', body.get('lean_messages', {}).get('errors', []))
            warnings = body.get('warnings', body.get('lean_messages', {}).get('warnings', []))
            tool_errors = body.get('tool_messages', {}).get('errors', [])
            small = {key: value for key, value in item.items() if key != 'body'}
            small.update(error_count=len(errors), warning_count=len(warnings),
                         tool_error_count=len(tool_errors), failed_declarations=body.get('failed_declarations', []),
                         error_type=body.get('error_type'), error=body.get('error'),
                         retryable=body.get('retryable'),
                         error_examples=[(e.get('message', '') if isinstance(e, dict) else e)[:1000] for e in errors[:2]],
                         tool_error_examples=tool_errors[:2])
            index.write(json.dumps(small, ensure_ascii=False)+'\n')
            index.flush()
            results.append(small)
            if len(results) % 100 == 0 or item['outcome'] not in ('passed', 'rejected'):
                print(len(results), '/', len(rows), dict(Counter(r['outcome'] for r in results)),
                      'elapsed=', round(time.monotonic()-started), flush=True)
    categories = defaultdict(Counter)
    for item in results:
        categories[item['category']][item['outcome']] += 1
    summary = {'count': len(results), 'outcomes': dict(Counter(r['outcome'] for r in results)),
               'by_category': {k: dict(v) for k, v in categories.items()},
               'elapsed_seconds': round(time.monotonic()-started, 3),
               'max_response_bytes': max(r['response_bytes'] for r in results),
               'unresolved': [r for r in results if r['outcome'] not in ('passed', 'rejected')],
               'finished_at': datetime.now(timezone.utc).isoformat()}
    Path(str(base)+'-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
