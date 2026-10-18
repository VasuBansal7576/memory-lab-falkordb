/* Current graph-only model matrix; historical runs remain separate below. */
(async () => {
  const get = async path => {
    const response = await fetch(path);
    if (!response.ok) throw Error(`Evidence unavailable (${response.status}): ${path}`);
    return response.json();
  };
  const make = (tag, text) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const format = value => typeof value === 'number' ? value.toLocaleString(undefined, {maximumFractionDigits: 2}) : '—';
  get('/proof-data/reference-parity/reference-rag.json').then(data => {
    document.querySelector('#rag-verification').textContent = data.status === 'passed'
      ? `Passed: ${data.source_count} original raw notes, ${data.sidecar_count} original distilled files and all ${data.cases.length} first-hit source checks. Imported quotations match their source; unchanged reimports do no work.`
      : 'Original RAG verification needs attention.';
  }).catch(error => { document.querySelector('#rag-verification').textContent = error.message; });
  try {
    const pointer = await get('/proof-data/model-comparison/latest.json');
    const base = '/proof-data/' + pointer.directory + '/';
    const [manifest, summary, runs] = await Promise.all(['manifest', 'summary', 'runs'].map(name => get(base + name + '.json')));
    const link = (label, path) => {
      const a = make('a', label); a.href = base + path; return a;
    };
    document.querySelector('#current-status').textContent = `${manifest.status} · ${summary.total_runs} calls · ${summary.invalid_runs} invalid · ${summary.decision_failures} decision failures · Codex subscription · low reasoning`;
    const table = make('table'), head = make('tr');
    ['Requested model', 'Retrieval', 'Decision checks', 'Seconds', 'Input', 'Cached input*', 'Output', 'Tools'].forEach(text => head.append(make('th', text)));
    table.append(head);
    for (const cell of summary.cells) {
      const row = make('tr');
      const values = [cell.model, cell.arm === 'graph' ? 'Graph only' : 'Search + read', `${cell.decisions_correct}/${cell.runs}`];
      for (const key of ['total_s', 'input_tokens', 'cached_input_tokens', 'output_tokens', 'tool_calls']) {
        const value = cell.approval[key];
        values.push(value ? `${format(value.median)} [${format(value.min)}–${format(value.max)}]` : '—');
      }
      values.forEach(text => row.append(make('td', text)));table.append(row);
    }
    document.querySelector('#current-metrics').replaceChildren(table);
    const failures=document.querySelector('#current-failures');
    for(const run of runs.filter(row=>!row.decision_correct)) {
      const message=run.valid ? `${run.grade.reasons.join('; ')}. Raw decision: ${JSON.stringify(run.decision)}` : 'Invalid: '+run.error;
      failures.append(make('li',`${run.model} / ${run.question_id} / repeat ${run.repeat}: ${message}`));
    }
    if(!failures.children.length)failures.append(make('li','No recorded failures.'));
    const links = document.querySelector('#current-links');
    for (const [label, path] of [['Frozen parameters', 'manifest.json'], ['All results', 'runs.json'], ['Summary', 'summary.json'], ['All-attempt token accounting', 'accounting.json']]) links.append(link(label, path));
    const select = document.querySelector('#current-question');
    for (const [id, question] of new Map(runs.map(run => [run.question_id, run.question]))) select.append(new Option(question, id));
    const render = () => {
      const cards = document.querySelector('#current-answers');cards.replaceChildren();
      for (const model of manifest.requested_models) for (const arm of ['search', 'graph']) {
        const run = runs.find(row => row.model === model && row.arm === arm && row.question_id === select.value);
        const card = make('div');card.className = 'card';
        card.append(make('h3', `${model} · ${arm === 'graph' ? 'graph only' : 'search + read'}`));
        if (run) {
          card.append(make('p', run.valid ? (run.decision_correct ? 'Decision and required sources passed.' : 'Decision failed: ' + run.grade.reasons.join('; ')) : 'Invalid: ' + run.error),
            make('pre', JSON.stringify(run.decision, null, 2)), make('p', 'First recorded response; timing statistics use all valid approval repeats.'));
          const sources = make('div');sources.className = 'links';
          sources.append(link('Exact prompt', run.input_path), link('Raw trace', run.trace_path));
          if (run.recall_path) sources.append(link('Graph evidence', run.recall_path));
          card.append(sources);
        } else card.append(make('p', 'No recorded run.'));
        cards.append(card);
      }
    };
    select.onchange = render;render();
    for (const limitation of summary.limitations) document.querySelector('#current-limitations').append(make('li', limitation));
  } catch (error) {
    document.querySelector('#current-status').textContent = 'No completed current comparison is available. ' + error.message;
  }
})();
