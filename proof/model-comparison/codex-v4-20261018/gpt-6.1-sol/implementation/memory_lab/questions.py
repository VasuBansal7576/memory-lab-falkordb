import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    kind: str
    sequences: tuple[tuple[tuple[str, bool], ...], ...] = ()


OWNER = (('OWNS', False),)
LEADER = (('OWNS', False), ('LEADS', False))
POLICIES = (('USES', True), ('GOVERNED_BY', True))
APPROVER_QUERY = (
    'MATCH p=(n0:Policy)-[:APPROVED_BY|HELD_BY*1..2]->(h:Person) '
    "WHERE n0.id=$seed AND type(relationships(p)[0])='APPROVED_BY' "
    "AND (length(p)=1 OR (nodes(p)[1].kind='Role' AND type(relationships(p)[1])='HELD_BY')) "
    'OPTIONAL MATCH (h)-[delegation:DELEGATES_TO]->(d:Person) '
    'RETURN CASE WHEN d IS NULL THEN nodes(p) ELSE nodes(p)+[d] END, '
    'CASE WHEN d IS NULL THEN relationships(p) ELSE relationships(p)+[delegation] END LIMIT 64'
)


def plan_question(question: str) -> Plan:
    q = question.casefold().strip()
    for kind, shape in SHAPES.items():
        if re.fullmatch(shape, q):
            sequences = {'owner': (OWNER,), 'leader': (LEADER,), 'policies': (POLICIES,)}
            return Plan(kind, sequences.get(kind, ()))
    if re.search(r'\b(connection|connected|relationship|path)\b', q) and re.search(r'\b(between|and|to)\b', q):
        return Plan('connection')
    if re.search(r'\b(lead|leads|leader)\b', q) and re.search(r'\b(own|owns|owner)\b', q):
        return Plan('leader', (LEADER,))
    if re.search(r'\b(own|owns|owner)\b', q):
        return Plan('owner', (OWNER,))
    if re.search(r'\b(policies|policy)\b', q) and re.search(r'\b(govern|governs|governing)\b', q) and re.search(r'\b(use|uses|used)\b', q):
        return Plan('policies', (POLICIES,))
    if re.search(r'\b(approve|approves|approver|approval)\b', q):
        return Plan('approver')
    if re.search(r'\b(describe|tell me about|what is|who is|show facts|summarize)\b', q):
        return Plan('describe')
    return Plan('unsupported')


def resolve_mentions(question: str, entities: list[dict]) -> list[list[dict]]:
    q = question.casefold()
    hits = []
    for entity in entities:
        for alias in {entity['id'], entity['name'], *entity['aliases']}:
            text = alias.casefold()
            # Accept singular wording of a multiword plural alias (Atlas release/releases).
            pattern = re.escape(text[:-1]) + 's?' if ' ' in text and text.endswith('s') else re.escape(text)
            for match in re.finditer(r'(?<!\w)' + pattern + r'(?!\w)', q):
                hits.append((match.start(), match.end(), entity))
    intervals = []
    for start, end, entity in sorted(hits, key=lambda x: (-(x[1] - x[0]), x[0], x[2]['id'])):
        same = next((i for i in intervals if i[0] == start and i[1] == end), None)
        if same:
            if entity['id'] not in {e['id'] for e in same[2]}:
                same[2].append(entity)
        elif not any(start < b and end > a for a, b, _ in intervals):
            intervals.append((start, end, [entity]))
    return [entities for _, _, entities in sorted(intervals)]


SHAPES = {
    'owner': r'(?:who owns (?:the )?@|who is (?:the )?owner of (?:the )?@)',
    'leader': r'(?:who leads (?:the )?team that owns (?:the )?@|who is (?:the )?leader of (?:the )?team that owns (?:the )?@)',
    'policies': r'which polic(?:y|ies) governs? (?:the )?services used by (?:the )?@',
    'approver': r'(?:who approves (?:the )?@|who is (?:the )?approver for (?:the )?@)',
    'describe': r'(?:describe @|tell me about @|what is @|who is @|show facts (?:about|for) @|summarize @)',
    'connection': r'(?:(?:(?:what is|show) (?:the )?)?(?:connection|relationship|path) between @ and @|how is @ connected to @)',
}


def question_shape(question: str, entities: list[dict]) -> str:
    aliases = {alias.casefold() for entity in entities for alias in [entity['id'], entity['name'], *entity['aliases']]}
    pattern = r'(?<!\w)(?:' + '|'.join(re.escape(a) for a in sorted(aliases, key=len, reverse=True)) + r')(?!\w)'
    shape = re.sub(pattern, '@', question.casefold()) if aliases else question.casefold()
    shape = re.sub(r'\s+', ' ', shape.strip().rstrip('?.!').strip())
    return shape


def supported_shape(question: str, entities: list[dict], kind: str) -> bool:
    return bool(re.fullmatch(SHAPES.get(kind, r'(?!)'), question_shape(question, entities)))
