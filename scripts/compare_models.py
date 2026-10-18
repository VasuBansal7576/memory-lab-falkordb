"""Two requested Codex models × two retrieval methods; keep every raw result."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.compare_graph import run_suite,write_json,sha,FIXTURE


def matrix_summary(models,rows):
    cells=[]
    for model,summary in models.items():
        for arm in ['search','graph']:
            value=summary['arms'][arm]
            cells.append({'model':model,'arm':arm,**value})
    return {'status':'observed_pilot','cells':cells,'total_runs':len(rows),
        'invalid_runs':sum(not r['valid'] for r in rows),
        'decision_failures':sum(not r['decision_correct'] for r in rows),
        'limitations':[
            'Two requested model IDs, not a measurement of parameter counts or intrinsic model intelligence.',
            '16 synthetic documents and six structured decision cases; not production-scale evidence.',
            'Each model runs both methods; approval order alternates search/graph across repeats. Model blocks run sequentially.',
            'Graph arm is graph-only: no BM25, vectors or distilled note hits. The baseline actually searches and reads the same raw corpus.',
            'Graph annotations are hand-authored against the raw source. Source presence/schema checks do not prove semantic entailment.',
            'Latency includes CLI startup, network and generation. Cache and service load can affect it.',
            'Input tokens include cached input; no subscription-dollar savings are inferred.',
            'Index build time is separate; write-time model extraction cost is not included in answer-time savings.',
            'Scoring covers verdict, identity and required source IDs; no free-text answer-quality score.',
            'Failed runs remain in the denominator. Latency/token medians describe valid approval repeats only.']}


def provider_accounting(output,rows):
    """Count observed usage even when an attempt failed its method contract."""
    calls=[];unknown=[]
    for row in rows:
        raw=(output/row['trace_path']).read_text();usage=[]
        for line in raw.splitlines():
            try:event=json.loads(line)
            except ValueError:continue
            value=event.get('usage',{})
            if event.get('type')=='turn.completed' and all(type(value.get(k)) is int for k in ['input_tokens','cached_input_tokens','output_tokens']):
                usage.append(value)
        if not usage:unknown.append(row['model']+'/'+row['run_id'])
        calls.append({'model':row['model'],'run_id':row['run_id'],'valid':row['valid'],
            'trace_sha256':sha(raw),'completed_turn_usage':usage})
    totals={key:sum(u[key] for call in calls for u in call['completed_turn_usage'])
        for key in ['input_tokens','cached_input_tokens','output_tokens']}
    return {'scope':'Derived from original raw traces, including invalid attempts. Grades and raw evidence are unchanged. Totals include only reported usage; missing usage is explicitly unknown.',
        'attempts':len(rows),'completed_provider_turns':sum(len(c['completed_turn_usage']) for c in calls),
        'reported_token_totals':totals,'usage_unavailable_for':unknown,'calls':calls}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--models',default='gpt-6.1-sol,gpt-6-luna')
    parser.add_argument('--fixture',default=str(FIXTURE))
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--port',type=int,default=6387)
    parser.add_argument('--timeout',type=int,default=120)
    args=parser.parse_args();models=args.models.split(',')
    if len(models)<2 or len(set(models))!=len(models) or any(not re.fullmatch(r'[a-zA-Z0-9_.-]+',m) for m in models):
        parser.error('Provide at least two distinct model IDs')
    if args.repeats<1 or args.timeout<1:parser.error('Positive repeats and timeout required')
    output=Path(args.output).resolve()
    if not output.is_relative_to(ROOT/'proof'):raise ValueError('Model comparison output must be under the project proof directory')
    if output.exists() and any(output.iterdir()):raise ValueError('Choose a fresh proof directory')
    output.mkdir(parents=True,exist_ok=True)
    # The graph-only experiment deliberately builds no semantic index.
    os.environ['MEMORY_SEMANTIC']='0'
    manifest={'version':4,'status':'running','created_at':datetime.now(timezone.utc).isoformat(),
        'requested_models':models,'provider':'Codex CLI / authenticated ChatGPT subscription',
        'retrieval':'graph-only vs actual local search/read','reasoning_effort':'low',
        'fixture_sha256':sha(Path(args.fixture).read_text()),'harness_sha256':sha(Path(__file__).read_text()),
        'parameters':vars(args),'models':{m:{'directory':m} for m in models}}
    write_json(output/'manifest.json',manifest)
    (output/'matrix-harness.py').write_text(Path(__file__).read_text())
    summaries={};rows=[]
    for model in models:
        suite_args=SimpleNamespace(output=str(output/model),fixture=args.fixture,port=args.port,
            model=model,repeats=args.repeats,timeout=args.timeout,question_ids=None,graph_mode='graph')
        run_suite(suite_args)
        summaries[model]=json.loads((output/model/'summary.json').read_text())
        for row in json.loads((output/model/'runs.json').read_text()):
            rows.append(row|{'model':model,'trace_path':model+'/'+row['trace_path'],
                'input_path':model+'/'+row['input_path'],
                'recall_path':f'{model}/raw/{row["run_id"]}.recall.json' if row['arm']=='graph' else None})
        write_json(output/'runs.json',rows)
        write_json(output/'summary.json',matrix_summary(summaries,rows))
    manifest['status']='complete';manifest['completed_at']=datetime.now(timezone.utc).isoformat()
    write_json(output/'accounting.json',provider_accounting(output,rows))
    write_json(output/'manifest.json',manifest)
    pointer=ROOT/'proof/model-comparison/latest.json'
    pointer.parent.mkdir(parents=True,exist_ok=True)
    write_json(pointer,{'directory':str(output.relative_to(ROOT/'proof')),'status':'complete'})
    print(json.dumps({'status':'complete','output':str(output),'runs':len(rows),
        'invalid':sum(not r['valid'] for r in rows),'correct':sum(r['decision_correct'] for r in rows)}))
    return 0 if all(r['valid'] for r in rows) else 1


if __name__=='__main__':raise SystemExit(main())
