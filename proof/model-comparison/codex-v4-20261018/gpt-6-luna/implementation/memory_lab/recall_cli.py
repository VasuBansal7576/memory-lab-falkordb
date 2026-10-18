"""Recall graph and notes with shared configuration; optional hooks never block."""
import argparse
import json
import sys
from .settings import open_memory,settings
from .context import hook_context


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('question',nargs='*');parser.add_argument('--hook',action='store_true')
    parser.add_argument('--json',action='store_true');parser.add_argument('--env-file')
    parser.add_argument('--mode',choices=['graph','rag','hybrid'])
    args=parser.parse_args()
    try:
        question=json.load(sys.stdin).get('prompt','') if args.hook else ' '.join(args.question)
        if not isinstance(question,str) or not question.strip():
            if args.hook:return
            parser.error('A nonempty question is required')
        evidence=open_memory(args.env_file).recall(question,mode=args.mode)
        if args.hook:
            print(json.dumps({'hookSpecificOutput':{'hookEventName':'UserPromptSubmit',
                'additionalContext':hook_context(evidence,settings(args.env_file))}},ensure_ascii=False))
        elif args.json:print(json.dumps(evidence,ensure_ascii=False,indent=2))
        else:print(evidence['evidence_text'])
    except Exception:
        if not args.hook:raise


if __name__=='__main__':main()
