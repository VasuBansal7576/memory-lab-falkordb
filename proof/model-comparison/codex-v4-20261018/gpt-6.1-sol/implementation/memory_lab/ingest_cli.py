import argparse
import json
from .settings import open_memory
from .notes import sync_notes


def main():
    p=argparse.ArgumentParser(description='Import a Markdown folder, optionally distil questions and extract typed graph facts.')
    p.add_argument('folder');p.add_argument('--env-file');p.add_argument('--distil',action='store_true');p.add_argument('--extract',action='store_true')
    p.add_argument('--limit',type=int,help='Maximum changed notes to process with models; all raw sources are indexed')
    p.add_argument('--extractions',help='Folder of reference-style source_doc/nodes/edges/aliases JSON')
    p.add_argument('--distilled',help='Folder of reference-style source/questions/summary/rule/quote Markdown sidecars')
    args=p.parse_args()
    print(json.dumps(sync_notes(open_memory(args.env_file),args.folder,distil=args.distil,extract=args.extract,limit=args.limit,extractions=args.extractions,distilled=args.distilled),indent=2))


if __name__=='__main__':main()
