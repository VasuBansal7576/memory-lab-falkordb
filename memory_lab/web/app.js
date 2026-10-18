'use strict';

const state = {
  status: null,
  snapshot: null,
  documents: [],
  investigations: [],
  selectedDocument: null,
  answer: null,
  phase: 'idle',
  sessionId: null,
};

const $ = (id) => document.getElementById(id);
const sampleQuestions = [
  'What is the onboarding process?',
  'How do we handle employee expenses?',
  'A customer needs a £900 refund on 15 October 2026. Who signs it off?',
  'Who signs off a £900 customer refund on 15 November 2026?',
  'Who may process a £200 customer refund without further approval?',
  'Who leads the team accountable for Beacon?',
  'Who is Alex?',
  'Who owns Nimbus?',
];

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  });
  let data;
  try { data = await response.json(); }
  catch { throw new Error('The server returned an unreadable response. Refresh and try again.'); }
  if (!response.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : Array.isArray(data.detail)
      ? data.detail.map((issue) => `${(issue.loc || []).filter((part) => part !== 'body').join('.')} ${issue.msg || 'is invalid'}`).join('; ')
      : 'The request failed. Refresh and try again.';
    throw new Error(detail);
  }
  return data;
}

function announce(message) { $('announcements').textContent = message; }

function feedback(id, message, kind = '') {
  const node = $(id);
  node.textContent = message;
  node.className = `form-feedback ${kind}`;
}

function setPhase(phase) {
  state.phase = phase;
  const busy = phase === 'asking';
  $('ask-button').disabled = busy;
  $('close-session').disabled = busy || !state.sessionId;
  $('ask-button').textContent = busy ? 'Reading the graph…' : 'Ask question ↗';
  $('answer-region').setAttribute('aria-busy', String(busy));
  $('answer-region').classList.toggle('loading', busy);
  for (const button of document.querySelectorAll('.sample-question, .saved-item')) button.disabled = busy;
}

function renderStatus() {
  const status = state.status;
  const connected = status.database === 'connected';
  $('database-status').replaceChildren(element('span', 'status-dot'), document.createTextNode(connected ? 'FalkorDB connected' : 'FalkorDB unavailable'));
  $('database-status').classList.toggle('unavailable', !connected);
  $('snapshot-label').textContent = `Snapshot ${status.snapshot ?? 'unavailable'}`;
  $('data-counts').textContent = `${status.document_count ?? 0} documents · ${status.entity_count ?? 0} entities · ${status.fact_count ?? 0} facts`;
  const modelOption = $('answer-mode').querySelector('[value="anthropic"]');
  const configured = Boolean(status.model?.configured && status.answer_modes?.includes('anthropic'));
  modelOption.disabled = !configured;
  modelOption.textContent = configured ? 'Anthropic synthesis' : 'Anthropic · not configured';
  $('model-configuration').hidden = configured;
  $('answer-mode').title = 'Evidence mode makes no model call. Codex uses local login; Anthropic requires local configuration.';
  const codexOption = $('answer-mode').querySelector('[value="codex"]');
  codexOption.disabled = !status.codex?.configured;
  codexOption.textContent = status.codex?.configured ? 'Codex · local login' : 'Codex · login required';
  $('model-configuration').hidden = configured || Boolean(status.codex?.configured);
  if (!status.answer_modes?.includes($('answer-mode').value)) $('answer-mode').value = 'evidence';
  updateModeHelp();
}

function updateModeHelp() {
  $('mode-help').textContent = $('answer-mode').value === 'evidence' ? 'No model call' : ($('answer-mode').value === 'codex' ? state.status?.codex?.model : state.status?.model?.model) || 'Uses configured model';
  document.querySelector('.mode-note').textContent = $('answer-mode').value === 'evidence' ? 'Evidence mode · no model call' : ($('answer-mode').value === 'codex' ? 'Codex · model call' : 'Anthropic synthesis · model call');
}

function renderSamples() {
  $('sample-questions').replaceChildren(...sampleQuestions.map((question) => {
    const button = element('button', 'sample-question');
    button.type = 'button';
    button.append(element('span', '', question), element('span', 'sample-arrow', '↗'));
    button.addEventListener('click', () => {
      $('question-input').value = question;
      ask(question);
    });
    return button;
  }));
}

function renderInvestigations() {
  $('saved-count').textContent = state.investigations.length || '';
  if (!state.investigations.length) {
    $('saved-list').replaceChildren(element('p', 'small muted', 'Save a question to return to it later.'));
    return;
  }
  $('saved-list').replaceChildren(...state.investigations.map((item) => {
    const button = element('button', 'saved-item', item.question);
    button.type = 'button';
    const date = new Date(item.created_at);
    const time = element('time', '', Number.isNaN(date.valueOf()) ? `Snapshot ${item.snapshot}` : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }));
    if (!Number.isNaN(date.valueOf())) time.dateTime = date.toISOString();
    button.append(time);
    button.addEventListener('click', () => ask(item.question, item.id));
    return button;
  }));
}

function renderDocuments() {
  const select = $('document-select');
  select.replaceChildren(...state.documents.map((doc) => {
    const option = element('option', '', doc.title);
    option.value = doc.id;
    return option;
  }));
  select.disabled = !state.documents.length;
  if (!state.documents.length) {
    select.append(element('option', '', 'No documents available'));
    $('document-preview').replaceChildren(element('p', 'document-placeholder', 'This workspace has no source documents.'));
    $('edit-details').hidden = true;
    return;
  }
  const selected = state.documents.find((doc) => doc.id === state.selectedDocument) || state.documents[0];
  selectDocument(selected.id);
}

function selectDocument(id, focus = false) {
  const doc = state.documents.find((item) => item.id === id);
  if (!doc) return;
  state.selectedDocument = doc.id;
  $('document-select').value = doc.id;
  const preview = $('document-preview');
  const stats = element('div', 'document-stats');
  stats.append(element('span', '', `${doc.entities?.length || 0} entities`), element('span', '', `${doc.facts?.length || 0} explicit facts`));
  const entities = element('div', 'entity-list');
  for (const entity of doc.entities || []) entities.append(element('span', 'entity-chip', entity.name));
  preview.replaceChildren(element('h3', 'document-heading', doc.title), element('p', 'document-label', doc.id), element('p', 'document-text', doc.text), stats, entities);
  $('document-json').value = JSON.stringify(doc, null, 2);
  $('edit-details').hidden = false;
  feedback('document-feedback', '');
  if (focus) {
    $('documents-title').scrollIntoView({ behavior: 'smooth', block: 'start' });
    $('document-select').focus({ preventScroll: true });
    announce(`Opened source document ${doc.title}.`);
  }
}

function renderAnswer(answer) {
  state.answer = answer;
  $('empty-state').hidden = true;
  $('answer-content').hidden = false;
  $('answered-question').textContent = answer.question;
  $('answer-text').textContent = answer.answer;
  const labels = { supported: 'Source supported', recalled: 'Related evidence · apply conditions', missing: 'Evidence missing', ambiguous: 'Needs clarification', unsupported: 'Outside supported questions' };
  $('answer-status').textContent = answer.mode !== 'evidence' && ['supported', 'recalled'].includes(answer.status) ? 'Model answer · inspect sources' : (labels[answer.status] || answer.status);
  $('answer-status').className = `answer-status ${answer.status}`;
  $('answer-meta').textContent = `Snapshot ${answer.snapshot} · ${answer.mode === 'evidence' ? 'Evidence mode · no model call' : `Model synthesis${answer.model ? ` · ${answer.model}` : ''}`}${state.snapshot !== null && String(answer.snapshot) !== String(state.snapshot) ? ' · A newer snapshot is available. Ask again to refresh.' : ''}`;
  $('save-button').disabled = false;
  $('save-button').textContent = 'Save investigation +';
  renderPaths(answer.paths || []);
  renderCitations(answer.citations || []);
  renderCandidates(answer.candidates || []);
  renderTrace(answer);
  for (const button of document.querySelectorAll('.sample-question')) button.classList.toggle('active', button.firstChild.textContent === answer.question);
}

function renderPaths(paths) {
  $('paths-region').hidden = !paths.length;
  $('path-list').replaceChildren(...paths.map((path) => {
    const row = element('div', 'path');
    (path.nodes || []).forEach((node, index) => {
      const box = element('div', 'path-node');
      box.append(element('small', '', node.kind || 'Entity'), element('strong', '', node.name || node.id));
      row.append(box);
      const edge = path.edges?.[index];
      if (edge) {
        const label = element('span', 'path-edge');
        const predicate = String(edge.predicate).replaceAll('_', ' ').toLowerCase();
        const references = edge.citation_ids?.length ? ` · ${edge.citation_ids.join(', ')}` : '';
        label.append(element('span', '', `${predicate}${references}`), element('span', '', edge.subject === node.id ? '→' : '←'));
        label.title = `${edge.subject} → ${edge.object}${edge.citation_ids?.length ? ` · ${edge.citation_ids.join(', ')}` : ''}`;
        row.append(label);
      }
    });
    return row;
  }));
}

function renderCitations(citations) {
  $('citations-region').hidden = !citations.length;
  $('citation-count').textContent = citations.length;
  $('citation-list').replaceChildren(...citations.map((citation) => {
    const card = element('div', 'citation');
    const head = element('div', 'citation-head');
    const source = element('button', 'citation-source', citation.title || citation.document_id);
    source.type = 'button';
    source.addEventListener('click', () => selectDocument(citation.document_id, true));
    head.append(element('span', 'citation-tag', citation.id), source);
    card.append(head, element('blockquote', '', citation.quote), element('p', 'citation-revision', `Document revision ${citation.revision ?? 'unspecified'}`));
    return card;
  }));
}

function renderCandidates(candidates) {
  $('candidate-region').hidden = !candidates.length;
  $('candidate-list').replaceChildren(...candidates.map((candidate) => {
    if (typeof candidate === 'string') return element('p', 'candidate', candidate);
    return element('p', 'candidate', `${candidate.name || candidate.id || 'Candidate'}${candidate.kind ? ` · ${candidate.kind}` : ''}${candidate.description ? ` · ${candidate.description}` : ''}${candidate.id ? ` · ${candidate.id}` : ''}`);
  }));
}

function renderTrace(answer) {
  const trace = answer.trace;
  $('trace-region').hidden = !trace;
  if (!trace) return;
  const retrieval = typeof trace.retrieval_ms === 'number' ? `${trace.retrieval_ms.toFixed(1)} ms` : 'Timing unavailable';
  $('trace-summary').textContent = `${retrieval} · ${trace.hop_count ?? 0} hops`;
  const facts = [
    ['Resolved seeds', (trace.seeds || []).map((seed) => typeof seed === 'string' ? seed : seed.name || seed.id || JSON.stringify(seed)).join(', ') || 'None'],
    ['Facts returned', trace.facts_returned ?? 'Unavailable'],
    ['Retrieval time', retrieval],
    ['Hop count', trace.hop_count ?? 'Unavailable'],
  ];
  $('trace-facts').replaceChildren(...facts.flatMap(([label, value]) => [element('dt', '', label), element('dd', '', value)]));
  $('cypher-query').textContent = trace.query || 'No graph query was executed for this question.';
  $('usage-details').textContent = answer.usage ? Object.entries(answer.usage).map(([key, value]) => `${key.replaceAll('_', ' ')} ${value}`).join(' · ') : 'Evidence retrieval uses no model tokens.';
}

async function ask(question, investigationId) {
  const clean = question.trim();
  if (!clean || state.phase === 'asking') return;
  $('question-input').value = clean;
  setPhase('asking');
  feedback('question-feedback', investigationId ? 'Resuming against the current graph…' : 'Retrieving source evidence…');
  try {
    if ($('answer-mode').value !== 'evidence' && !state.sessionId) state.sessionId = (await api('/api/sessions', {method:'POST'})).id;
    const answer = await api(investigationId ? `/api/investigations/${encodeURIComponent(investigationId)}/resume` : '/api/ask', {
      method: 'POST', body: JSON.stringify(investigationId ? { mode: $('answer-mode').value, session_id: state.sessionId } : { question: clean, mode: $('answer-mode').value, session_id: state.sessionId }),
    });
    renderAnswer(answer);
    $('close-session').disabled = !state.sessionId;
    feedback('question-feedback', '');
    announce(`Answer ready. ${answer.citations?.length || 0} source citations. ${answer.status}.`);
  } catch (error) {
    feedback('question-feedback', `${error.message} Your question is kept above. Try again when the database is available.`, 'error');
    announce('The question could not be answered.');
  } finally { setPhase('idle'); }
}

async function refresh() {
  $('refresh-button').disabled = true;
  $('global-error').hidden = true;
  const results = await Promise.allSettled([api('/api/status'), api('/api/documents'), api('/api/investigations')]);
  const [status, documents, investigations] = results;
  if (status.status === 'fulfilled') {
    state.status = status.value;
    state.snapshot = status.value.snapshot;
    renderStatus();
  } else {
    $('database-status').replaceChildren(element('span', 'status-dot'), document.createTextNode('Connection unavailable'));
    $('database-status').classList.add('unavailable');
    $('snapshot-label').textContent = 'Snapshot unavailable';
    $('data-counts').textContent = 'Workspace data unavailable';
  }
  if (documents.status === 'fulfilled') {
    state.documents = documents.value.documents || [];
    state.snapshot = documents.value.snapshot;
    renderDocuments();
  }
  if (investigations.status === 'fulfilled') {
    state.investigations = Array.isArray(investigations.value) ? investigations.value : investigations.value.investigations || [];
    renderInvestigations();
  } else $('saved-list').replaceChildren(element('p', 'small muted', 'Saved investigations are unavailable. Refresh to retry.'));
  const failure = results.find((result) => result.status === 'rejected');
  if (failure || state.status?.database === 'unavailable') {
    $('global-error').textContent = `Workspace connection needs attention. ${failure ? failure.reason.message : 'FalkorDB is unavailable. Start the local database, then refresh data.'}`;
    $('global-error').hidden = false;
  }
  await memoryStatus();
  if (state.answer) renderAnswer(state.answer);
  $('refresh-button').disabled = false;
}

$('question-form').addEventListener('submit', (event) => { event.preventDefault(); ask($('question-input').value); });
$('question-input').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); $('question-form').requestSubmit(); }
});
$('refresh-button').addEventListener('click', refresh);
$('answer-mode').addEventListener('change', updateModeHelp);
$('document-select').addEventListener('change', (event) => selectDocument(event.target.value));
$('save-button').addEventListener('click', async () => {
  if (!state.answer) return;
  $('save-button').disabled = true;
  try {
    await api('/api/investigations', { method: 'POST', body: JSON.stringify({ question: state.answer.question }) });
    const saved = await api('/api/investigations');
    state.investigations = Array.isArray(saved) ? saved : saved.investigations || [];
    renderInvestigations();
    $('save-button').textContent = 'Investigation saved ✓';
    announce('Investigation saved. Resume it from the saved investigations list.');
  } catch (error) {
    $('save-button').disabled = false;
    feedback('question-feedback', `Could not save investigation. ${error.message}`, 'error');
  }
});
$('document-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  let documentData;
  try {
    documentData = JSON.parse($('document-json').value);
    if (!documentData || Array.isArray(documentData) || typeof documentData !== 'object') throw new Error('The document must be a JSON object.');
    if (documentData.id !== state.selectedDocument) throw new Error('Keep the document id unchanged when replacing a source.');
  } catch (error) { feedback('document-feedback', `Check the JSON. ${error.message}`, 'error'); return; }
  $('document-save').disabled = true;
  feedback('document-feedback', 'Saving source and rebuilding its facts…');
  try {
    const result = await api(`/api/documents/${encodeURIComponent(state.selectedDocument)}`, { method: 'PUT', body: JSON.stringify({ document: documentData, expected_snapshot: state.snapshot }) });
    await refresh();
    feedback('document-feedback', result.changed ? `Saved. Graph snapshot ${result.snapshot}. Ask again to use the updated evidence.` : 'No changes. This document already matches the saved source.', 'success');
    announce('Document saved.');
  } catch (error) { feedback('document-feedback', `${error.message} Your JSON remains in the editor.`, 'error'); }
  finally { $('document-save').disabled = false; }
});

renderSamples();
refresh();

async function memoryStatus() {
  try {
    const data = await api('/api/memory/status');
    const jobs = data.jobs || [];
    const failed = jobs.filter(j => j.status === 'failed').length;
    const pending = jobs.filter(j => !['indexed','failed'].includes(j.status)).length;
    feedback('memory-feedback', `${jobs.filter(j => j.status === 'indexed').length} sessions indexed · ${pending} pending${failed ? ` · ${failed} need retry` : ''}`, failed ? 'error' : '');
  } catch (error) { feedback('memory-feedback', error.message, 'error'); }
}
$('close-session').addEventListener('click', async () => {
  if (!state.sessionId || state.phase === 'asking') return;
  $('close-session').disabled = true;
  try {
    const result = await api(`/api/sessions/${state.sessionId}/close`, {method:'POST'});
    state.sessionId = null;
    feedback('memory-feedback', result.status === 'disabled' ? 'Session capture is disabled in workspace settings.' : result.status === 'too_short' ? 'No model conversation to save.' : 'Session queued. Refresh data to check indexing progress.');
  } catch (error) { feedback('memory-feedback', error.message, 'error'); $('close-session').disabled = false; }
});
$('sync-notes').addEventListener('click', async () => {
  $('sync-notes').disabled = true;
  try { await api('/api/notes/sync', {method:'POST'}); await refresh(); }
  catch (error) { feedback('memory-feedback', error.message, 'error'); }
  finally { $('sync-notes').disabled = false; }
});
window.addEventListener('pagehide', () => {
  if (state.sessionId) navigator.sendBeacon(`/api/sessions/${state.sessionId}/close`, new Blob(['{}'], {type:'application/json'}));
});
setInterval(memoryStatus, 10000);

$('retry-memory').addEventListener('click', async () => {
  try { await api('/api/memory/process', {method:'POST'}); feedback('memory-feedback','Pending sessions queued for retry.'); }
  catch (error) { feedback('memory-feedback', error.message, 'error'); }
});
