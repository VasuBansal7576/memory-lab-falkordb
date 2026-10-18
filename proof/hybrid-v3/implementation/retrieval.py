"""Heading-aware source chunks, BM25 ranking, local embeddings and rank fusion."""
from collections import Counter
from functools import lru_cache
import hashlib
import math
import re
from threading import Lock

MODEL = 'BAAI/bge-small-en-v1.5'
DIMENSIONS = 384
STOPWORDS = set('a an and are as at be by can do does for from how i in is it me my of on or our that the their there this to was we what when where which who why with would you your own owns owner tell about'.split())
_lock = Lock()


@lru_cache(maxsize=1)
def embedding_model():
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=MODEL, threads=2)


@lru_cache(maxsize=4096)
def embed(text):
    with _lock:
        return [float(v) for v in next(iter(embedding_model().embed([text])))]


def tokens(text):
    return [t for t in re.findall(r'\w+',text.casefold()) if t not in STOPWORDS]


def chunks(document, budget=1400):
    text = document['text']
    # A heading starts a section; offsets always refer to the original source.
    boundaries = [m.start() for m in re.finditer(r'(?m)^#{1,6}\s+',text)]
    boundaries = sorted(set([0,*boundaries,len(text)]))
    out = []
    for a,b in zip(boundaries,boundaries[1:]):
        heading = text[a:b].splitlines()[0].lstrip('# ').strip() if text[a:b].startswith('#') else document['title']
        cursor = a
        while cursor < b:
            end = min(cursor+budget,b)
            if end < b:
                cut = text.rfind('\n',cursor+budget//2,end)
                if cut > cursor:
                    end = cut+1
            quote = text[cursor:end]
            if quote.strip():
                out.append({'id':hashlib.sha256(f"{document['id']}:{cursor}:{end}".encode()).hexdigest(),
                    'document_id':document['id'],'title':document['title'],'section':heading,
                    'quote':quote,'text':f"{document['title']} / {heading}\n{quote}",
                    'start':cursor,'end':end,'kind':'chunk'})
            cursor = end
    for i,entry in enumerate(document.get('distilled',[])):
        offset = text.index(entry['quote'])
        out.append({'id':hashlib.sha256(f"{document['id']}:distilled:{i}".encode()).hexdigest(),
            'document_id':document['id'],'title':document['title'],'section':'distilled question',
            'quote':entry['quote'],'text':entry['question']+'\n'+entry.get('summary','')+'\n'+entry['quote'],
            'start':offset,'end':offset+len(entry['quote']),'kind':'distilled'})
    return out


def bm25(rows,question,k=10):
    query = set(tokens(question))
    if not query or not rows:
        return []
    bags = [Counter(tokens(row['text'])) for row in rows]
    sizes = [sum(b.values()) for b in bags]
    average = sum(sizes)/len(sizes) or 1
    document_frequency = Counter(t for bag in bags for t in bag)
    scores = []
    for row,bag,size in zip(rows,bags,sizes):
        score = 0
        for term in query:
            frequency = bag[term]
            if not frequency:
                continue
            inverse = math.log(1+(len(rows)-document_frequency[term]+.5)/(document_frequency[term]+.5))
            score += inverse*frequency*2.2/(frequency+1.2*(.25+.75*size/average))
        if score:
            scores.append((score,row['id']))
    return [id for _,id in sorted(scores,key=lambda x:(-x[0],x[1]))[:k]]


def fuse(legs,k=60):
    scores,found = {},{}
    for name,ids in legs.items():
        for rank,id in enumerate(dict.fromkeys(ids),start=1):
            scores[id] = scores.get(id,0)+1/(k+rank)
            found.setdefault(id,[]).append(name)
    return sorted(scores,key=lambda id:(-scores[id],id)),found,scores
