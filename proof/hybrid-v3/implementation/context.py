"""Small, inspectable evidence packets. UI provenance stays outside model context."""


def compact_evidence(evidence, document_ids=False):
    citations = {c['id']: c for c in evidence.get('citations', [])}
    def marker(ids):
        values = [citations[i]['document_id'] if document_ids else i for i in ids]
        return '[' + ','.join(dict.fromkeys(values)) + ']'
    lines, seen = [], set()
    for path in evidence.get('paths', []):
        names = {n['id']: n['name'] for n in path['nodes']}
        for edge in path['edges']:
            line = (f"{marker(edge['citation_ids'])} {names.get(edge['subject'], edge['subject'])}"
                    f" --{edge['predicate']}--> {names.get(edge['object'], edge['object'])}")
            if line not in seen:
                seen.add(line)
                lines.append(line)
    for note in evidence.get('notes', []):
        line = f"{marker([note['citation_id']])} {note['name']}: {note['text']}"
        if line not in seen:
            seen.add(line)
            lines.append(line)
    # Preserve every source quotation, even when a triple or note was emitted.
    # Conditions can occur in the quote without a separate description annotation.
    for citation in citations.values():
        quote = citation['quote']
        if not any(quote in line for line in lines):
            lines.append(f"{marker([citation['id']])} Source: {quote}")
    for candidate in evidence.get('candidates', []):
        lines.append(f"Candidate {candidate['id']}: {candidate['name']}; aliases: {', '.join(candidate.get('aliases', []))}")
    if evidence.get('trace', {}).get('truncated'):
        lines.append('WARNING: retrieval limit reached; evidence may be incomplete. Do not assume omitted facts are absent.')
    return '\n'.join(lines) or 'No matching graph evidence.'
