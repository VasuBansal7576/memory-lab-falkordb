"""Controlled local document-search versus FalkorDB recall pilot.

Only structured decisions are scored. No prose-quality or model-tier claim.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from memory_lab.memory import Memory
from memory_lab.context import compact_evidence
from memory_lab.decisions import (SCHEMA, COMMON, FEATURE_ARGS, sanitize_stderr, validate_response, parse_trace, build_prompt, run_model)

ARMS = ('search', 'graph')
FIXTURE = ROOT / 'data/operations.json'


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()






def grade_response(gold, response, documents):
    """Grade an explicit decision, not prose mentioning the expected name."""
    validate_response(response)
    identities = {}
    for document in documents:
        for entity in document['entities']:
            for label in [entity['id'], entity['name'], *entity['aliases']]:
                identities.setdefault(label.casefold(), set()).add(entity['id'])
    def resolve(labels):
        result = set()
        for label in labels:
            ids = identities.get(label.casefold(), set())
            if len(ids) != 1:
                return None
            result.update(ids)
        return result
    expected, actual = resolve(gold['entities']), resolve(response['entities'])
    reasons = []
    if gold['verdict'] != response['verdict']:
        reasons.append('wrong verdict')
    if actual is None or actual != expected:
        reasons.append('wrong or ambiguous entity identity')
    cited = set(response['source_ids'])
    if not cited <= {d['id'] for d in documents}:
        reasons.append('unknown source ID')
    if not set(gold['sources']) <= cited:
        reasons.append('missing required supporting document')
    return {'decision_correct': not reasons, 'reasons': reasons,
            'scope': 'Entity identity, verdict and required evidence; no free-text answer quality score.'}








def metric(values):
    return {'median': statistics.median(values), 'min': min(values), 'max': max(values)} if values else None


def summarize(rows):
    arms = {}
    for arm in ARMS:
        chosen = [r for r in rows if r['arm'] == arm]
        repeated = [r for r in chosen if r['question_id'] == 'october-refund' and r['valid']]
        stats = {}
        for field in ('total_s', 'recall_s', 'input_tokens', 'cached_input_tokens', 'output_tokens', 'tool_calls', 'tool_output_bytes', 'prompt_utf8_bytes'):
            stats[field] = metric([r[field] for r in repeated])
        arms[arm] = {'runs': len(chosen), 'valid': sum(r['valid'] for r in chosen),
                     'decisions_correct': sum(r['decision_correct'] for r in chosen),
                     'approval_repeats': len(repeated), 'approval': stats}
    graph_tokens = [r['input_tokens'] for r in rows if r['arm'] == 'graph' and r['question_id'] == 'october-refund' and r['valid']]
    return {'status': 'observed_pilot', 'arms': arms, 'total_runs': len(rows),
            'failures': sum(not r['valid'] for r in rows),
            'graph_repeated_input_token_range': max(graph_tokens)-min(graph_tokens) if graph_tokens else None,
            'limitations': ['16 synthetic documents, six question cases; not production-scale evidence.',
                'Same requested model and low reasoning effort. No small-versus-large model claim.',
                'End-to-end timing includes CLI startup, network and generation; graph timing includes recall.',
                'Both arms have the same raw corpus available; search must use tools, graph must use the recalled packet without tools.',
                'Plugins, apps, skill discovery, hooks and browser tools are disabled identically. Hidden CLI overhead remains.',
                'Order is counterbalanced across repeats. Service load and caching remain possible confounders.',
                'Input token totals include cached input; never add cached tokens again.',
                'Scoring covers structured entity decisions and required source IDs, not prose entailment.',
                'Graph annotation and indexing costs are excluded and must be assessed separately.']}


def write_json(path, value):
    path.write_text(encode(value) + '\n')


def run_suite(args):
    output = Path(args.output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Choose a fresh output directory; evidence is never overwritten')
    output.mkdir(parents=True, exist_ok=True)
    (output/'raw').mkdir()
    (output/'inputs').mkdir()
    fixture = json.loads(Path(args.fixture).read_text())
    documents, questions = fixture['documents'], fixture['questions']
    selected = args.question_ids.split(',') if args.question_ids else list(questions)
    if not set(selected) <= set(questions):
        raise ValueError('Unknown question ID')
    memory = Memory(port=args.port, namespace='comparison_' + uuid.uuid4().hex)
    index_started = time.perf_counter()
    memory.ensure_seed(Path(args.fixture))
    index_s = time.perf_counter() - index_started
    snapshot = memory.overview()['snapshot']
    graph_mode=getattr(args,'graph_mode','graph')
    manifest = {'version': 4, 'status': 'running', 'created_at': datetime.now(timezone.utc).isoformat(),
                'model': args.model, 'reasoning_effort': 'low', 'fixture_sha256': sha(Path(args.fixture).read_text()),
                'harness_sha256': sha(Path(__file__).read_text()),
                'decision_engine_sha256': sha((ROOT/'memory_lab/decisions.py').read_text()), 'snapshot': snapshot, 'index_s': index_s,
                'retrieval': 'Typed graph only; no BM25/vector note hits' if graph_mode=='graph' else 'Typed graph plus BM25/vector notes',
                'graph_mode':graph_mode,
                'document_count': len(documents), 'raw_source_utf8_bytes': sum(len(d['text'].encode()) for d in documents),
                'parameters': vars(args), 'feature_args': FEATURE_ARGS, 'common_instructions': COMMON,
                'schema': SCHEMA, 'gold': questions,
                'scoring': 'Structured decisions only. No free-text answer is requested or graded.',
                'codex_version': subprocess.run(['codex','--version'],capture_output=True,text=True).stdout.strip()}
    write_json(output/'manifest.json', manifest)
    write_json(output/'corpus.json', fixture)
    implementation=output/'implementation';(implementation/'memory_lab').mkdir(parents=True)
    files=[*sorted((ROOT/'memory_lab').glob('*.py')),Path(__file__)]
    manifest['implementation_sha256']={str(p.relative_to(ROOT)):sha(p.read_text()) for p in files}
    for p in files:
        target=implementation/p.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
    write_json(output/'manifest.json',manifest)
    rows, pair = [], 0
    with tempfile.TemporaryDirectory(prefix='memory-eval-') as temp:
        directory = Path(temp)
        (directory/'corpus').mkdir()
        for doc in documents:
            (directory/'corpus'/f'{doc["id"]}.txt').write_text(f'Document ID: {doc["id"]}\nTitle: {doc["title"]}\n\n{doc["text"]}\n')
        schema = directory/'decision.schema.json'
        write_json(schema, SCHEMA)
        # Both arms use this exact same directory, tools, model and corpus.
        for qid in selected:
            gold = questions[qid]
            for repeat in range(1, (args.repeats if qid == 'october-refund' else 1) + 1):
                order = ARMS if pair % 2 == 0 else tuple(reversed(ARMS))
                pair += 1
                for arm in order:
                    run_id = f'{qid}-{repeat}-{arm}'
                    row = {'run_id': run_id, 'question_id': qid, 'question': gold['question'], 'arm': arm,
                           'repeat': repeat, 'order': list(order), 'valid': False, 'decision_correct': False,
                           'decision': None, 'grade': None, 'error': None, 'recall_s': 0,
                           'input_tokens': 0, 'cached_input_tokens': 0, 'output_tokens': 0,
                           'tool_calls': 0, 'tool_output_bytes': 0, 'prompt_utf8_bytes': 0,
                           'trace_path': f'raw/{run_id}.jsonl', 'input_path': f'inputs/{run_id}.txt'}
                    started = time.perf_counter()
                    try:
                        if memory.overview()['snapshot'] != snapshot:
                            raise ValueError('Frozen graph changed')
                        evidence = ''
                        if arm == 'graph':
                            t = time.perf_counter()
                            recalled = memory.recall(gold['question'],mode=graph_mode)
                            if graph_mode=='graph' and 'search_legs' in recalled['trace']:
                                raise ValueError('Graph-only experiment received note retrieval')
                            evidence = compact_evidence(recalled, document_ids=True,max_chars=6000)
                            row['recall_s'] = time.perf_counter()-t
                            write_json(output/'raw'/f'{run_id}.recall.json', recalled)
                        prompt = build_prompt(gold['question'], arm, evidence)
                        row['prompt_utf8_bytes'] = len(prompt.encode())
                        (output/row['input_path']).write_text(prompt)
                        result = run_model(prompt, schema, args.model, args.timeout, directory)
                        (output/row['trace_path']).write_text(result['stdout'])
                        (output/'raw'/f'{run_id}.stderr.txt').write_text(result['stderr'])
                        row['cli_s'] = result['elapsed_s']
                        if result['returncode'] != 0:
                            raise ValueError(f'CLI failed or timed out: {result["returncode"]}')
                        decision, usage, commands = parse_trace(result['stdout'], arm)
                        row.update({key: usage[key] for key in ('input_tokens','cached_input_tokens','output_tokens')})
                        row['usage'] = usage
                        row['tool_calls'] = len(commands)
                        row['tool_output_bytes'] = sum(len(c.get('aggregated_output','').encode()) for c in commands)
                        row['decision'] = decision
                        row['grade'] = grade_response(gold, decision, documents)
                        row['decision_correct'] = row['grade']['decision_correct']
                        row['valid'] = True
                        if memory.overview()['snapshot'] != snapshot:
                            raise ValueError('Frozen graph changed during call')
                    except Exception as exc:
                        row.update(valid=False, decision_correct=False, error=f'{type(exc).__name__}: {exc}')
                    row['total_s'] = time.perf_counter()-started
                    rows.append(row)
                    write_json(output/'runs.json', rows)
                    write_json(output/'summary.json', summarize(rows))
                    print(f'{run_id}: valid={row["valid"]} decision={row["decision_correct"]} tools={row["tool_calls"]} error={row["error"]}',flush=True)
    manifest['status'] = 'complete'
    manifest['completed_at'] = datetime.now(timezone.utc).isoformat()
    write_json(output/'manifest.json', manifest)
    return 0 if all(r['valid'] for r in rows) else 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', default='proof/current')
    p.add_argument('--fixture', default=str(FIXTURE))
    p.add_argument('--port', type=int, default=6387)
    p.add_argument('--model', default='gpt-6.1-sol')
    p.add_argument('--repeats', type=int, default=6)
    p.add_argument('--timeout', type=int, default=120)
    p.add_argument('--question-ids')
    p.add_argument('--graph-mode',choices=['graph','hybrid'],default='graph')
    args = p.parse_args()
    if args.repeats < 1 or args.timeout < 1:
        p.error('Positive repeats and timeout required')
    return run_suite(args)


if __name__ == '__main__':
    raise SystemExit(main())
