from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Kind = Literal['Person', 'Team', 'Service', 'Project', 'Policy', 'Role', 'Process', 'Document']
Predicate = Literal['OWNS', 'LEADS', 'USES', 'DEPENDS_ON', 'GOVERNED_BY', 'APPROVED_BY', 'HELD_BY', 'DELEGATES_TO', 'PART_OF', 'REFERENCES']
PREDICATES = {
    'OWNS': ({'Team', 'Person'}, {'Service', 'Project'}),
    'LEADS': ({'Person'}, {'Team'}),
    'USES': ({'Project', 'Service'}, {'Service'}),
    'DEPENDS_ON': ({'Service', 'Project'}, {'Service', 'Project'}),
    'GOVERNED_BY': ({'Service', 'Project'}, {'Policy'}),
    'APPROVED_BY': ({'Policy'}, {'Role', 'Person'}),
    'HELD_BY': ({'Role'}, {'Person'}),
    'DELEGATES_TO': ({'Person'}, {'Person'}),
    'PART_OF': (set(Kind.__args__), set(Kind.__args__)),
    'REFERENCES': (set(Kind.__args__), set(Kind.__args__)),
}


class Entity(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    id: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_.-]+$')
    kind: Kind
    name: str = Field(min_length=1, max_length=160)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    description: str = Field(default='', max_length=2000)

    @field_validator('name')
    @classmethod
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError('Name must not be blank')
        return value

    @field_validator('aliases')
    @classmethod
    def valid_aliases(cls, values):
        if any(not x.strip() or len(x) > 160 for x in values):
            raise ValueError('Aliases must be nonempty and at most 160 characters')
        return sorted(set(values))


class Fact(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    subject: str
    predicate: Predicate
    object: str
    quote: str = Field(min_length=1, max_length=4000)


class DistilledEntry(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    question: str = Field(min_length=1, max_length=500)
    summary: str = Field(default='', max_length=1000)
    quote: str = Field(min_length=1, max_length=4000)


class Document(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    id: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_.-]+$')
    title: str = Field(min_length=1, max_length=240)
    text: str = Field(min_length=1, max_length=100000)
    entities: list[Entity] = Field(max_length=200)
    facts: list[Fact] = Field(max_length=500)
    origin: str = Field(default='', max_length=1000)
    distilled: list[DistilledEntry] = Field(default_factory=list, max_length=50)
    processed: list[Literal['distilled', 'extracted']] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_evidence(self):
        if not self.title.strip() or not self.text.strip():
            raise ValueError('Title and text must not be blank')
        by_id = {e.id: e for e in self.entities}
        for entry in self.distilled:
            if not entry.quote.strip() or entry.quote not in self.text:
                raise ValueError('Distilled quote must be an exact source substring')
        for entity in self.entities:
            if entity.description and entity.description not in self.text:
                raise ValueError('Entity description must be an exact source substring')
        if len(by_id) != len(self.entities):
            raise ValueError('Entity IDs must be unique within a document')
        for fact in self.facts:
            if fact.subject not in by_id or fact.object not in by_id:
                raise ValueError('Every fact endpoint must be declared in this document')
            source_types, target_types = PREDICATES[fact.predicate]
            if by_id[fact.subject].kind not in source_types or by_id[fact.object].kind not in target_types:
                raise ValueError(f'Invalid endpoint types for {fact.predicate}')
            if not fact.quote.strip() or fact.quote not in self.text:
                raise ValueError('Each quote must be an exact nonblank substring of document text')
        return self
