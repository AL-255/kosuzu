'use strict';
const $ = id => document.getElementById(id);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {settings:null, inventory:null, draft:null, currentView:'inventory', stockId:null, stockRequest:null, loading:false, queue:[], setupStep:null, connectionChecked:false, boxDraft:null, boxRequest:null};
const names = {inventory:'Inventory', boxes:'Boxes', import:'New component', requests:'My requests', queue:'Error queue', settings:'Settings'};
const UNBOXED = '__unboxed__';
const boxName = id => id ? state.inventory?.boxes[id]?.name || 'Unknown box' : 'Unboxed';
const encodeBox = id => id || UNBOXED;
const decodeBox = id => id === UNBOXED ? '' : id;
function boxOptions(ids, counts=null) { return ids.map(id => `<option value="${escapeHTML(encodeBox(id))}">${escapeHTML(boxName(id))}${counts ? ` · ${number(counts[id] || 0)} pieces` : ''}</option>`).join(''); }
function allBoxIds() { return ['', ...Object.keys(state.inventory?.boxes || {}).sort((a,b) => boxName(a).localeCompare(boxName(b)))]; }

async function api(path, body) {
  const options = {headers:{'X-Kosuzu':'1'}, credentials:'same-origin'};
  if (body !== undefined) { options.method = 'POST'; options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
  let response;
  try { response = await fetch('/api/' + path, options); }
  catch { throw new Error('Connection unavailable. Local requests remain saved; reconnect and sync requests.'); }
  const value = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== 'login') signedOut();
    throw new Error(value.error || `Request failed (${response.status})`);
  }
  return value;
}

function signedOut() {
  $('workspace').hidden = true; $('login-view').hidden = false;
  state.settings = null; state.draft = null; state.inventory = null;
  state.setupStep = null; state.connectionChecked = false;
}
function showError(error, id='global-error') { $(id).textContent = error.message || String(error); $(id).hidden = false; if (id === 'global-error' && state.setupStep !== null) $(id).scrollIntoView({block:'start'}); }
function notice(message) { $('notice').textContent = message; $('notice').hidden = false; }
function clearMessages() { $('notice').hidden = true; $('global-error').hidden = true; }
function number(value) { return Number(value).toLocaleString(); }
function httpsLink(value) { try { return new URL(value).protocol === 'https:' ? value : ''; } catch { return ''; } }
function githubLink(number) { return `https://github.com/${state.settings.repo}/pull/${number}`; }

async function busy(button, task, pending) {
  const original = button.textContent;
  button.disabled = true; if (pending) button.textContent = pending;
  try { return await task(); }
  finally { button.disabled = false; button.textContent = original; }
}

function showView(view) {
  if (!names[view]) view = 'inventory';
  if (view === 'queue' && state.settings?.mode !== 'server') view = 'inventory';
  state.currentView = view;
  if (view !== 'settings' && state.setupStep !== null) { state.setupStep = null; renderSetup(); }
  document.querySelectorAll('.view').forEach(section => { section.hidden = section.id !== 'view-' + view; });
  document.querySelectorAll('.nav').forEach(button => button.classList.toggle('active', button.dataset.view === view));
  $('breadcrumb').textContent = 'Workspace / ' + names[view];
  if (location.hash !== '#' + view) history.replaceState(null, '', '#' + view);
  if (view === 'requests') loadRequests().catch(showError);
  if (view === 'queue') loadQueue().catch(showError);
  if (view === 'boxes' && !state.inventory && state.settings?.client_token_saved) loadInventory().catch(showError);
}

function renderSettings() {
  const s = state.settings;
  $('pages-enabled').checked = !!s.pages_enabled; renderPages(s.pages_status);
  $('setting-repo').value = s.repo; $('setting-branch').value = s.branch;
  $('database-settings').hidden = s.role !== 'admin';
  $('server-token-label').hidden = s.mode !== 'server';
  $('llm-hosts-label').hidden = s.role !== 'admin';
  $('queue-nav').hidden = s.mode !== 'server';
  $('sync-server').hidden = s.role !== 'admin';
  $('server-token').placeholder = s.server_token_saved ? 'Saved · leave blank to keep' : 'Dedicated server GitHub token';
  $('client-token').placeholder = s.client_token_saved ? 'Saved · leave blank to keep' : 'Dedicated client GitHub token';
  $('llm-key').placeholder = s.llm_key_saved ? 'Saved · leave blank to keep' : 'Your provider API key';
  $('llm-url').value = s.llm.base_url || 'https://api.deepseek.com';
  $('llm-model').value = s.llm.model || 'deepseek-flash';
  $('llm-enabled').checked = s.features.llm_enabled;
  $('llm-fallback').checked = s.features.llm_fallback;
  $('llm-hosts').value = s.llm_hosts.join(', ');
  $('repo-label').textContent = s.repo || 'Set up your database';
  $('mode-label').textContent = `${s.mode === 'server' ? 'Server workspace' : 'Desktop client'} · ${s.role}`;
  const selected = $('supplier').value;
  const enabled = s.suppliers.filter(supplier => s.features.suppliers[supplier.key].enabled);
  $('supplier').innerHTML = enabled.map(supplier => `<option value="${escapeHTML(supplier.key)}">${escapeHTML(supplier.name)}</option>`).join('') + '<option value="manual">Enter details manually</option>';
  $('supplier').value = enabled.some(v => v.key === selected) || selected === 'manual' ? selected : enabled.some(v => v.key === 'lcsc') ? 'lcsc' : enabled[0]?.key || 'manual';
  $('supplier-credentials').innerHTML = s.suppliers.map(supplier => {
    const key = escapeHTML(supplier.key), options = s.features.suppliers[supplier.key];
    return `<div class="supplier-box" data-supplier-box="${key}"><label class="feature-switch"><input type="checkbox" role="switch" data-feature-supplier="${key}" data-switch="enabled" ${options.enabled ? 'checked' : ''}><span>${escapeHTML(supplier.name)}<small>${supplier.credential_fields.length ? s.supplier_api_ready[supplier.key] ? 'API credentials saved' : 'Can try public pages · API credentials optional' : 'Public product pages · no API key needed'}</small></span></label><div data-supplier-options ${options.enabled ? '' : 'hidden'}>${supplier.credential_fields.length ? `<label class="feature-switch"><input type="checkbox" role="switch" data-feature-supplier="${key}" data-switch="use_api" ${options.use_api ? 'checked' : ''}><span>Use supplier API when configured<small>Turn off to use public pages only.</small></span></label><label class="feature-switch" data-fallback-label ${options.use_api ? '' : 'hidden'}><input type="checkbox" role="switch" data-feature-supplier="${key}" data-switch="public_fallback" ${options.public_fallback ? 'checked' : ''}><span>Allow public-page fallback<small>Try public pages if API credentials are missing or the API is unavailable.</small></span></label><details data-api-fields ${options.use_api ? '' : 'hidden'}><summary>Add or update API credentials (optional)</summary><div class="form-grid">${supplier.credential_fields.map(field => `<label>${escapeHTML(field.replaceAll('_',' '))}<input type="password" autocomplete="new-password" data-supplier="${key}" data-field="${escapeHTML(field)}" placeholder="Blank keeps saved credentials"></label>`).join('')}</div></details>` : `<p class="small">${supplier.key === 'lcsc' ? 'Use the C-code printed on your bag, such as C25804.' : 'Use the numeric product ID, such as 3406.'}</p>`}</div></div>`;
  }).join('');
  $('shared-database').hidden = s.role === 'admin';
  $('shared-database').textContent = s.repo ? `Your workspace uses ${s.repo} (${s.branch}). The administrator manages this connection.` : 'Your administrator needs to connect the workspace repository before you can continue.';
  renderFeatureControls(); renderImportMode(); renderSetup();
}

function renderFeatureControls() {
  const enabled = $('llm-enabled').checked;
  $('llm-fields').hidden = !enabled; $('llm-fallback-label').hidden = !enabled;
  $('llm-status').textContent = !enabled ? 'Manual review selected. No LLM requests will be made.' : $('llm-fallback').checked ? 'Without a key, you can still import and check details yourself.' : 'LLM review is required. Imports stop if no key is saved or the provider fails.';
  document.querySelectorAll('[data-supplier-box]').forEach(box => {
    const active = box.querySelector('[data-switch="enabled"]').checked;
    box.querySelector('[data-supplier-options]').hidden = !active;
    const api = box.querySelector('[data-switch="use_api"]');
    if (api) { box.querySelector('[data-api-fields]').hidden = !api.checked; box.querySelector('[data-fallback-label]').hidden = !api.checked; }
  });
}

function renderImportMode() {
  const manual = $('supplier').value === 'manual';
  $('manual-fields').hidden = !manual; $('supplier-code-label').hidden = manual; $('product-url-label').hidden = manual;
  $('supplier-code').disabled = manual; $('product-url').disabled = manual;
  document.querySelectorAll('#manual-fields input, #manual-fields textarea').forEach(input => { input.disabled = !manual; });
  $('supplier-code').required = !manual;
  ['manual-manufacturer','manual-mpn','manual-description'].forEach(id => { $(id).required = manual; });
  const features = state.settings.features, willReview = features.llm_enabled && state.settings.llm_key_saved;
  $('lookup-button').textContent = manual ? 'Prepare for review →' : willReview ? 'Retrieve & review with LLM →' : 'Retrieve for manual review →';
  const options = features.suppliers[$('supplier').value];
  const supplier = state.settings.suppliers.find(s => s.key === $('supplier').value);
  const method = manual ? 'Enter details from the manufacturer datasheet. No supplier lookup will run.' : !supplier.credential_fields.length || !options.use_api ? 'This lookup uses public product pages.' : state.settings.supplier_api_ready[supplier.key] ? `This lookup uses the supplier API${options.public_fallback ? ', with public-page fallback if unavailable' : ''}.` : options.public_fallback ? 'No complete API credentials saved. This lookup will try public product pages.' : 'Add all supplier API credentials in Settings, or enable public-page fallback.';
  $('import-mode-note').textContent = method + (willReview ? ' LLM review uses your saved provider key.' : features.llm_enabled && !features.llm_fallback ? ' LLM review is required: add a key in Settings before importing.' : ' You will check the details yourself before saving.');
}

const setupSteps = ['GitHub connection','Suppliers','Review preferences','Finish'];
function renderSetup() {
  if (!state.settings) return;
  const wizard = state.setupStep !== null, step = state.setupStep;
  $('setup-guide').hidden = !wizard; $('setup-actions').hidden = !wizard;
  $('pages-settings').hidden = wizard || state.settings.mode !== 'server' || state.settings.role !== 'admin';
  $('settings-actions').hidden = wizard; $('start-setup').hidden = wizard;
  $('settings-title').textContent = wizard ? 'Let’s set up your workspace' : 'Connections & settings';
  document.querySelectorAll('[data-setup-step]').forEach(panel => {
    const number = Number(panel.dataset.setupStep);
    panel.hidden = wizard ? number !== step : number === 3;
    if (panel.id === 'database-settings' && state.settings.role !== 'admin') panel.hidden = true;
  });
  $('initialize').parentElement.hidden = wizard;
  $('setup-initialize').hidden = state.settings.role !== 'admin';
  if (!wizard) return;
  $('setup-progress').innerHTML = setupSteps.map((label, i) => `<li ${i === step ? 'aria-current="step"' : ''} class="${i === step ? 'active' : i < step ? 'complete' : ''}"><span>${i < step ? '✓' : i + 1}</span>${label}</li>`).join('');
  $('setup-description').textContent = ['First, connect GitHub. These are the only credentials you need to get started.', 'Choose the suppliers you use. You can skip every API key and add them later.', 'Choose manual review or optional LLM help. Every import still needs your confirmation.', 'Your preferences are saved. Check your database, then add your first component.'][step];
  $('setup-back').hidden = step === 0;
  $('setup-next').textContent = step === 3 ? 'Finish setup →' : 'Save & continue →';
  $('setup-next').disabled = step === 3 && !state.connectionChecked;
  const s = state.settings, suppliers = s.suppliers.filter(v => s.features.suppliers[v.key].enabled).map(v => v.name).join(', ');
  $('setup-summary').innerHTML = `<dl class="setup-summary"><dt>Database</dt><dd>${escapeHTML(s.repo || 'Not connected')} · ${escapeHTML(s.branch)}</dd><dt>Suppliers</dt><dd>${escapeHTML(suppliers || 'Manual entry only')}</dd><dt>Review</dt><dd>${!s.features.llm_enabled ? 'Manual review' : !s.llm_key_saved ? s.features.llm_fallback ? 'Manual review until you add an LLM key' : 'LLM required · add a key before importing' : s.features.llm_fallback ? 'LLM review with manual fallback' : 'LLM review required'}</dd></dl>`;
}

function openSetup() {
  clearMessages(); state.setupStep = state.settings.onboarding_complete ? 0 : state.settings.onboarding_step;
  state.connectionChecked = false; $('connection-result').textContent = '';
  showView('settings'); renderSetup(); focusSetup();
}

function focusSetup() { $('settings-title').setAttribute('tabindex','-1'); $('settings-title').focus({preventScroll:true}); $('settings-title').scrollIntoView({block:'start'}); }

async function boot() {
  try {
    state.settings = await api('settings');
    $('login-view').hidden = true; $('workspace').hidden = false; renderSettings();
    if (!state.settings.onboarding_complete) openSetup();
    else showView(location.hash.slice(1) || 'inventory');
    if (state.settings.client_token_saved && state.settings.repo && state.settings.onboarding_complete) await loadInventory();
    else if (state.settings.onboarding_complete) { openSetup(); notice('Reconnect GitHub to get started.'); }
    if (state.settings.mode === 'server' && state.settings.repo) await loadQueue();
  } catch (error) { if (state.settings) showError(error); }
}

async function loadInventory() {
  const result = await api('inventory');
  state.inventory = result.inventory;
  $('offline-label').hidden = !result.stale;
  if (result.stale) notice(`Showing cached inventory from ${new Date(result.time * 1000).toLocaleString()}. ${result.warning}`);
  renderInventory(); renderBoxes();
}

function renderInventory() {
  if (!state.inventory) return;
  const all = Object.values(state.inventory.components);
  $('stat-types').textContent = number(all.length);
  $('stat-pieces').textContent = number(all.reduce((total, row) => total + row.quantity, 0));
  $('stat-low').textContent = number(all.filter(row => row.quantity < 10).length);
  $('stat-revision').textContent = number(state.inventory.revision);
  const selectedCategory = $('category-filter').value;
  const categories = [...new Set(all.map(row => row.component.category).filter(Boolean))].sort();
  $('category-filter').innerHTML = '<option value="">All categories</option>' + categories.map(c => `<option value="${escapeHTML(c)}">${escapeHTML(c)}</option>`).join('');
  $('category-filter').value = categories.includes(selectedCategory) ? selectedCategory : '';
  const query = $('search').value.trim().toLowerCase();
  const selectedBox = $('box-filter').value;
  $('box-filter').innerHTML = '<option value="">All boxes</option>' + boxOptions(allBoxIds());
  $('box-filter').value = selectedBox;
  const boxFilter = $('box-filter').value;
  const rows = all.filter(row => {
    const c = row.component;
    return (!query || [c.mpn,c.manufacturer,c.description,c.location,c.supplier_code,c.category,c.package,...Object.values(c.attributes),...Object.keys(row.boxes).map(boxName)].join(' ').toLowerCase().includes(query)) && (!boxFilter || Object.hasOwn(row.boxes, decodeBox(boxFilter))) && (!$('category-filter').value || c.category === $('category-filter').value) && (!$('low-filter').checked || row.quantity < 10);
  }).sort((a,b) => a.component.mpn.localeCompare(b.component.mpn));
  $('inventory-rows').innerHTML = rows.map(row => {
    const c = row.component, image = httpsLink(c.image_url);
    const placements = Object.entries(row.boxes).map(([id,count]) => `<span class="box-allocation">${escapeHTML(boxName(id))}: ${number(count)}</span>`).join('');
    const count = boxFilter ? row.boxes[decodeBox(boxFilter)] : row.quantity;
    return `<tr><td><div class="component-cell">${image ? `<img class="part-img" src="${escapeHTML(image)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : '<span class="part-img part-placeholder" aria-hidden="true">▧</span>'}<div><strong>${escapeHTML(c.mpn)}</strong><small>${escapeHTML(c.manufacturer)}</small><small>${escapeHTML(c.description)}</small><span class="cell-secondary">${escapeHTML(c.supplier)} · ${escapeHTML(c.supplier_code)}</span></div></div></td><td>${escapeHTML(c.package || '—')}<div class="cell-secondary">${escapeHTML(c.category || 'Uncategorized')}</div></td><td><div class="box-allocations">${placements}</div>${c.location ? `<div class="cell-secondary">${escapeHTML(c.location)}</div>` : ''}</td><td><span class="stock-count ${count < 10 ? 'stock-low' : ''}">${number(count)}</span><div class="cell-secondary">${boxFilter ? `in this box · ${number(row.quantity)} total` : 'pieces'}</div></td><td><button class="secondary" data-adjust="${escapeHTML(c.id)}">Adjust stock</button></td></tr>`;
  }).join('');
  $('inventory-empty').hidden = rows.length > 0;
  if (!rows.length && all.length) { $('inventory-empty').querySelector('h3').textContent = 'No matching components'; $('inventory-empty').querySelector('p').textContent = 'Try another search or clear the filters.'; $('empty-settings').hidden = true; }
  else { $('inventory-empty').querySelector('h3').textContent = 'Your next build starts here'; $('inventory-empty').querySelector('p').textContent = 'Import your first component from its supplier part number.'; $('empty-settings').hidden = false; }
  $('result-count').textContent = `${number(rows.length)} of ${number(all.length)} components`;
}

function renderBoxes() {
  if (!state.inventory) return;
  $('box-list').innerHTML = allBoxIds().map(id => {
    const b = id ? state.inventory.boxes[id] : {name:'Unboxed',description:'Stock without a box assignment. Move it into a named box when you are ready.'};
    const rows = Object.values(state.inventory.components).filter(row => Object.hasOwn(row.boxes,id));
    const quantity = rows.reduce((sum,row) => sum + row.boxes[id],0), image = httpsLink(b.image_url);
    return `<article class="box-card">${image ? `<img src="${escapeHTML(image)}" alt="${escapeHTML(b.name)}" referrerpolicy="no-referrer" loading="lazy">` : '<div class="box-picture" aria-hidden="true">▣</div>'}<div><h2>${escapeHTML(b.name)}</h2><p>${escapeHTML(b.description)}</p><p class="small">${number(rows.length)} component types · ${number(quantity)} pieces</p><div class="actions"><button class="secondary" data-box-contents="${escapeHTML(encodeBox(id))}">View contents</button>${id ? `<button class="text-button" data-edit-box="${escapeHTML(id)}">Edit box</button>` : ''}</div></div></article>`;
  }).join('');
}

function openBox(id=null) {
  state.boxDraft = id ? {...state.inventory.boxes[id]} : {id:crypto.randomUUID().replaceAll('-',''),name:'',image_url:'',description:''};
  state.boxRequest = null; state.boxOriginal = id ? {...state.boxDraft} : null;
  $('box-dialog-title').textContent = id ? 'Edit box' : 'New box';
  ['name','image','description'].forEach(field => { $('box-' + field).value = state.boxDraft[field === 'image' ? 'image_url' : field]; });
  $('box-error').textContent = ''; $('box-dialog').showModal();
}

function renderStockBoxes(reset=false) {
  const row = state.inventory.components[state.stockId], action = $('stock-action').value;
  const previous = reset ? '' : $('stock-box').value;
  const ids = action === 'add' ? allBoxIds() : Object.keys(row.boxes);
  $('stock-box').innerHTML = '<option value="">Choose a box…</option>' + boxOptions(ids,row.boxes);
  $('stock-box').value = ids.some(id => encodeBox(id) === previous) ? previous : Object.keys(row.boxes).length === 1 ? encodeBox(Object.keys(row.boxes)[0]) : '';
  $('stock-box-prompt').hidden = Object.keys(row.boxes).length < 2;
  $('stock-box-prompt').textContent = 'This part belongs to multiple boxes. Choose which box you are putting parts into or taking them from.';
  $('stock-box-label').textContent = action === 'transfer' ? 'Source box' : action === 'remove' ? 'Take from box' : 'Put into box';
  $('stock-to-label').hidden = action !== 'transfer'; $('stock-to-box').required = action === 'transfer';
  const target = reset ? '' : $('stock-to-box').value;
  $('stock-to-box').innerHTML = '<option value="">Choose a destination…</option>' + boxOptions(allBoxIds(),row.boxes);
  $('stock-to-box').value = target;
}

function openStock(ident) {
  const row = state.inventory.components[ident];
  state.stockId = ident; state.stockRequest = null;
  $('stock-part').textContent = `${row.component.manufacturer} · ${row.component.mpn}`;
  $('stock-current').textContent = `${number(row.quantity)} pieces total · ${Object.entries(row.boxes).map(([id,count]) => boxName(id) + ': ' + number(count)).join(' · ')}`;
  $('stock-error').textContent = ''; $('stock-action').value = 'add'; $('stock-quantity').value = '1'; $('stock-note').value = '';
  renderStockBoxes(true);
  $('stock-dialog').showModal();
}

function transactionNotice(result) {
  const messages = {applied:'Your request is applied to the inventory.', pending:'Request submitted to GitHub. Your server will validate and apply it.', queued:'Request saved locally. It will retry when GitHub is available.', blocked:'Your request needs a correction. Read its error below and ask the server administrator to reject it before submitting a replacement.', rejected:'This request was rejected. Check the server error queue and submit a corrected request.'};
  notice(messages[result.status] || 'Request saved.');
  showView('requests');
}

function renderReview(result) {
  state.draft = result;
  const c = result.component;
  $('review-panel').hidden = false; $('review-step').classList.add('active');
  for (const field of ['manufacturer','mpn','description','category','package','location']) $('part-' + field).value = c[field];
  $('part-attributes').value = JSON.stringify(c.attributes, null, 2);
  $('part-confirmed').checked = false; $('part-note').value = ''; $('part-quantity').value = 1;
  $('part-box').innerHTML = boxOptions(allBoxIds()); $('part-box').value = $('box-filter').value || UNBOXED;
  $('original-data').textContent = JSON.stringify(result.original, null, 2);
  $('review-warnings').innerHTML = '<strong>Review notes</strong><ul>' + c.review.warnings.map(w => `<li>${escapeHTML(w)}</li>`).join('') + '</ul>';
  const image = httpsLink(c.image_url);
  $('review-image').hidden = !image;
  if (image) $('review-image').src = image; else $('review-image').removeAttribute('src');
  const source = httpsLink(c.source_url); $('source-link').hidden = !source; if (source) $('source-link').href = source;
  $('review-panel').scrollIntoView({behavior:'smooth', block:'start'});
}

async function loadRequests() {
  const rows = await api('outbox');
  $('request-list').innerHTML = rows.length ? rows.map(row => {
    const e = row.event, r = row.result;
    const c = e.component || state.inventory?.components[e.component_id]?.component;
    const label = e.kind === 'transfer' ? `Move ${number(e.quantity)} pieces · ${boxName(e.from_box)} → ${boxName(e.to_box)}` : e.box ? e.kind === 'box_create' ? 'New box' : 'Box edit' : `${e.kind === 'create' ? 'New component' : 'Stock adjustment'} · ${e.delta > 0 ? '+' : ''}${number(e.delta)} pieces${Object.hasOwn(e,'box_id') ? ' · ' + boxName(e.box_id) : ''}`;
    return `<article class="request-card"><div><h3>${escapeHTML(e.box?.name || c?.mpn || e.component_id)} <span class="status ${escapeHTML(r.status)}">${escapeHTML(r.status)}</span></h3><p>${escapeHTML(label)} · ${escapeHTML(row.repo)}</p><p>${escapeHTML(e.note)}</p>${r.error ? `<p class="error">${escapeHTML(r.error)}</p>` : ''}<p class="small">${escapeHTML(e.id)} · ${new Date(row.updated * 1000).toLocaleString()}</p></div><div>${r.url ? `<a href="${escapeHTML(httpsLink(r.url))}" target="_blank" rel="noopener noreferrer">View request #${r.number} ↗</a>` : '<span class="small">Waiting for connection</span>'}</div></article>`;
  }).join('') : '<div class="panel empty"><h3>No requests yet</h3><p>Import a component or adjust stock to send your first request.</p></div>';
}

async function loadQueue() {
  if (state.settings?.mode !== 'server') return;
  const result = await api('queue'); state.queue = result.errors;
  renderPages(result.pages);
  const open = result.errors.filter(e => e.status === 'open');
  $('queue-count').textContent = open.length; $('queue-count').hidden = !open.length;
  $('queue-warning').hidden = !open.length;
  $('queue-warning').textContent = `${open.length} unresolved ${open.length === 1 ? 'problem needs' : 'problems need'} attention. Open the Error queue to fix failed requests.`;
  $('sync-info').textContent = result.last_sync ? `Last completed sync: ${new Date(result.last_sync.time * 1000).toLocaleString()}` : 'No server sync completed yet. Save the server token and initialize your database.';
  $('error-list').innerHTML = result.errors.length ? result.errors.map(error => `<article class="request-card"><div><h3>${error.number ? 'Request #' + error.number : 'Server synchronization'} <span class="status ${error.status === 'open' ? 'rejected' : 'applied'}">${escapeHTML(error.status)}</span></h3><p class="${error.status === 'open' ? 'error' : ''}">${escapeHTML(error.message)}</p><p class="small">Updated ${new Date(error.updated * 1000).toLocaleString()}</p></div><div class="actions">${error.number ? `<a href="${escapeHTML(githubLink(error.number))}" target="_blank" rel="noopener noreferrer">View on GitHub ↗</a>` : ''}${error.status === 'open' && error.number && state.settings.role === 'admin' ? `<button class="secondary" data-queue-action="retry" data-number="${error.number}">Retry</button><button class="secondary" data-queue-action="reject" data-number="${error.number}">Reject request</button>` : ''}</div></article>`).join('') : '<div class="panel empty"><h3>All clear</h3><p>Unresolvable proposals and synchronization failures will appear here.</p></div>';
}

$('login-form').addEventListener('submit', async event => {
  event.preventDefault(); $('login-error').textContent = '';
  try { await busy(event.submitter, async () => { await api('login', {key:$('access-key').value}); $('access-key').value = ''; await boot(); }, 'Signing in…'); }
  catch (error) { showError(error,'login-error'); }
});
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => { clearMessages(); showView(button.dataset.view); }));
$('new-component').addEventListener('click', () => { clearMessages(); showView('import'); });
$('empty-settings').addEventListener('click', () => showView(state.settings?.client_token_saved ? 'import' : 'settings'));
$('logout').addEventListener('click', async () => { try { await api('logout',{}); signedOut(); } catch(error) { showError(error); } });
const mobileLogout = document.createElement('button');
mobileLogout.className = 'text-button mobile-logout'; mobileLogout.textContent = 'Sign out';
mobileLogout.addEventListener('click', () => $('logout').click());
document.querySelector('.topbar').append(mobileLogout);
window.addEventListener('hashchange', () => { if (state.settings) showView(location.hash.slice(1)); });
$('search').addEventListener('input', renderInventory); $('category-filter').addEventListener('change', renderInventory); $('low-filter').addEventListener('change',renderInventory);
$('box-filter').addEventListener('change',renderInventory);
$('refresh-boxes').addEventListener('click', async event => { clearMessages(); try { await busy(event.currentTarget,loadInventory,'Refreshing…'); } catch(error) { showError(error); } });
$('new-box').addEventListener('click', () => openBox());
$('box-list').addEventListener('click', event => {
  const edit = event.target.closest('[data-edit-box]'), contents = event.target.closest('[data-box-contents]');
  if (edit) openBox(edit.dataset.editBox);
  if (contents) { $('box-filter').value = contents.dataset.boxContents; renderInventory(); showView('inventory'); }
});
$('box-list').addEventListener('error', event => {
  if (event.target.tagName === 'IMG') { const placeholder = document.createElement('div'); placeholder.className = 'box-picture'; placeholder.textContent = '▣'; placeholder.setAttribute('aria-hidden','true'); event.target.replaceWith(placeholder); }
},true);
$('close-box').addEventListener('click', () => $('box-dialog').close());
['box-name','box-image','box-description'].forEach(id => $(id).addEventListener('input', () => { state.boxRequest = null; }));
$('box-form').addEventListener('submit', async event => {
  event.preventDefault(); $('box-error').textContent = '';
  state.boxRequest ||= crypto.randomUUID().replaceAll('-','');
  try { await busy(event.submitter,async () => {
    const result = await api('box',{request_id:state.boxRequest,box_id:state.boxDraft.id,name:$('box-name').value.trim(),image_url:$('box-image').value.trim(),description:$('box-description').value.trim(),previous:state.boxOriginal});
    $('box-dialog').close(); transactionNotice(result);
  },'Submitting…'); } catch(error) { showError(error,'box-error'); }
});
$('refresh').addEventListener('click', async event => { clearMessages(); try { await busy(event.currentTarget, loadInventory, 'Refreshing…'); } catch (error) { showError(error); } });
$('inventory-rows').addEventListener('click', event => { const button = event.target.closest('[data-adjust]'); if (button) openStock(button.dataset.adjust); });
$('inventory-rows').addEventListener('error', event => { if (event.target.tagName === 'IMG') event.target.hidden = true; }, true);
$('close-stock').addEventListener('click', () => $('stock-dialog').close());
['stock-action','stock-quantity','stock-note','stock-box','stock-to-box'].forEach(id => $(id).addEventListener('input', () => { state.stockRequest = null; }));
$('stock-action').addEventListener('change', () => renderStockBoxes());
$('stock-form').addEventListener('submit', async event => {
  event.preventDefault(); $('stock-error').textContent = '';
  if (!state.stockRequest) state.stockRequest = crypto.randomUUID().replaceAll('-','');
  try { await busy(event.submitter, async () => {
    const transfer = $('stock-action').value === 'transfer';
    const data = {request_id:state.stockRequest, component_id:state.stockId, note:$('stock-note').value};
    if (transfer) Object.assign(data,{quantity:Number($('stock-quantity').value),from_box:decodeBox($('stock-box').value),to_box:decodeBox($('stock-to-box').value)});
    else Object.assign(data,{delta:Number($('stock-quantity').value) * ($('stock-action').value === 'remove' ? -1 : 1),box_id:decodeBox($('stock-box').value)});
    const result = await api(transfer ? 'transfer' : 'adjust',data); $('stock-dialog').close(); transactionNotice(result);
  }); }
  catch (error) { showError(error, 'stock-error'); }
});
$('import-form').addEventListener('submit', async event => {
  event.preventDefault(); clearMessages();
  try { await busy(event.submitter, async () => {
    const data = {supplier:$('supplier').value, code:$('supplier-code').value.trim(), product_url:$('product-url').value.trim()};
    if (data.supplier === 'manual') data.manual = {manufacturer:$('manual-manufacturer').value.trim(), mpn:$('manual-mpn').value.trim(), description:$('manual-description').value.trim(), datasheet_url:$('manual-datasheet').value.trim()};
    const result = await api('import', data); renderReview(result);
  }, 'Preparing your review…'); }
  catch(error) { showError(error); }
});
$('discard-review').addEventListener('click', () => { state.draft = null; $('review-panel').hidden = true; $('review-step').classList.remove('active'); });
$('review-form').addEventListener('submit', async event => {
  event.preventDefault(); clearMessages();
  try { await busy(event.submitter, async () => {
    const edits = Object.fromEntries(['manufacturer','mpn','description','category','package','location'].map(k => [k,$('part-' + k).value]));
    try { edits.attributes = JSON.parse($('part-attributes').value); } catch { throw new Error('Electrical attributes must be a valid JSON object.'); }
    const result = await api('create', {draft_id:state.draft.draft_id, confirmed:$('part-confirmed').checked, quantity:Number($('part-quantity').value), box_id:decodeBox($('part-box').value), edits, note:$('part-note').value});
    state.draft = null; $('review-panel').hidden = true; $('review-step').classList.remove('active'); transactionNotice(result);
  }, 'Submitting…'); } catch(error) { showError(error); }
});
async function saveSettings(step=null, extra={}) {
  const data = {...extra};
  if (step === null && state.settings.mode === 'server' && state.settings.role === 'admin') data.pages_enabled = $('pages-enabled').checked;
  if (step === null || step === 0) {
    if ($('client-token').value.trim()) data.client_token = $('client-token').value.trim();
    if (state.settings.role === 'admin') {
      data.repo = $('setting-repo').value.trim(); data.branch = $('setting-branch').value.trim();
      data.llm_hosts = $('llm-hosts').value.split(',').map(v => v.trim()).filter(Boolean);
      if ($('server-token').value.trim()) data.server_token = $('server-token').value.trim();
    }
    if (step === 0 && (!(data.repo || state.settings.repo) || !(data.client_token || state.settings.client_token_saved) || (state.settings.mode === 'server' && state.settings.role === 'admin' && !(data.server_token || state.settings.server_token_saved)))) throw new Error('Enter your repository and required GitHub tokens. Supplier and LLM keys can wait.');
  }
  if (step === null || step === 2) {
    data.features = {llm_enabled:$('llm-enabled').checked, llm_fallback:$('llm-fallback').checked};
    if (data.features.llm_enabled) {
      data.llm = {base_url:$('llm-url').value.trim(), model:$('llm-model').value.trim()};
      if ($('llm-key').value.trim()) data.llm.api_key = $('llm-key').value.trim();
      if (step === 2 && !data.features.llm_fallback && !(data.llm.api_key || state.settings.llm_key_saved)) throw new Error('Add an LLM key, enable manual fallback, or turn off LLM review to continue.');
    }
  }
  if (step === null || step === 1) {
    data.features ||= {}; data.features.suppliers = {};
    document.querySelectorAll('[data-feature-supplier]').forEach(input => { data.features.suppliers[input.dataset.featureSupplier] ||= {}; data.features.suppliers[input.dataset.featureSupplier][input.dataset.switch] = input.checked; });
    const suppliers = {};
    document.querySelectorAll('[data-supplier]').forEach(input => { if (input.value.trim()) { suppliers[input.dataset.supplier] ||= {}; suppliers[input.dataset.supplier][input.dataset.field] = input.value.trim(); } });
    if (Object.keys(suppliers).length) data.suppliers = suppliers;
  }
  state.settings = await api('settings', data);
  if (step === null || step === 0) { state.connectionChecked = false; $('connection-result').textContent = ''; }
  ['client-token','server-token','llm-key'].forEach(id => { $(id).value = ''; });
  renderSettings(); $('settings-saved').textContent = 'Saved.';
}

function renderPages(status={}) {
  status ||= {};
  $('pages-status').textContent = status.status ? `Publication: ${status.status}${status.time ? ' · ' + new Date(status.time * 1000).toLocaleString() : ''}` : 'Not published yet.';
  $('pages-error').hidden = !status.error && !status.warning; $('pages-error').textContent = status.error || status.warning || ''; $('pages-error').className = status.error ? 'error' : 'small';
  const url = httpsLink(status.url); $('pages-link').hidden = !url; if (url) $('pages-link').href = url;
}
for (const [id,action] of [['publish-pages','publish'],['unpublish-pages','unpublish']]) $(id).addEventListener('click', async event => {
  clearMessages();
  try { await busy(event.currentTarget, async () => {
    if (action === 'publish') {
      if (!$('pages-enabled').checked) throw new Error('Enable public catalog publishing first. The exported inventory will be publicly readable.');
      state.settings = await api('settings', {pages_enabled:true});
    }
    const status = await api('pages', {action});
    state.settings = await api('settings'); renderSettings();
    if (status.error) throw new Error(status.error);
    notice(action === 'publish' ? status.status === 'prepared' ? 'Catalog files are ready. Follow the Pages configuration instructions below.' : 'Catalog submitted to GitHub Pages. The first deployment can take a few minutes.' : 'Website unpublished. Exported files remain in the repository.');
  }, action === 'publish' ? 'Publishing…' : 'Unpublishing…'); } catch (error) { showError(error); }
});

$('settings-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (state.setupStep !== null) { $('setup-next').click(); return; }
  clearMessages(); $('settings-saved').textContent = '';
  try { await busy(event.submitter, async () => { await saveSettings(); notice('Preferences saved.'); }, 'Saving…'); }
  catch(error) { showError(error); }
});
['llm-enabled','llm-fallback'].forEach(id => $(id).addEventListener('change', renderFeatureControls));
$('supplier-credentials').addEventListener('change', renderFeatureControls);
$('supplier').addEventListener('change', () => { state.draft = null; $('review-panel').hidden = true; renderImportMode(); });
$('start-setup').addEventListener('click', openSetup);
$('setup-back').addEventListener('click', () => { clearMessages(); state.setupStep -= 1; renderSetup(); focusSetup(); });
$('setup-later').addEventListener('click', () => { state.setupStep = null; renderSettings(); notice('Saved steps are kept. Open step-by-step setup to continue.'); });
$('setup-next').addEventListener('click', async event => {
  clearMessages(); const step = state.setupStep;
  try { await busy(event.currentTarget, async () => {
    if (step === 3) {
      if (!state.connectionChecked) throw new Error('Check your saved GitHub connection first.');
      await saveSettings(3, {onboarding_complete:true});
      state.setupStep = null; renderSetup(); showView('inventory'); await loadInventory();
      notice('Your workspace is ready. Add your first component, or adjust stock on an existing part.');
    } else {
      await saveSettings(step, {onboarding_step:step + 1});
      state.setupStep = step + 1; renderSetup(); focusSetup();
    }
  }, 'Saving…'); }
  catch(error) { showError(error); }
  finally { renderSetup(); }
});
$('check-connection').addEventListener('click', async event => {
  clearMessages(); state.connectionChecked = false;
  try { await busy(event.currentTarget, async () => {
    const result = await api('connection', {}); state.connectionChecked = true;
    $('connection-result').textContent = `Connected to ${state.settings.repo}. ${result.components} component types · revision ${result.revision}.`;
  }, 'Checking…'); }
  catch(error) { $('connection-result').textContent = ''; showError(error); }
  finally { renderSetup(); }
});
$('setup-initialize').addEventListener('click', async event => {
  clearMessages();
  try { await busy(event.currentTarget, async () => { await api('initialize', {}); $('connection-result').textContent = 'Database ready. Check the connection to continue.'; state.connectionChecked = false; renderSetup(); }, 'Initializing…'); }
  catch(error) { showError(error); }
});
$('initialize').addEventListener('click', async event => { clearMessages(); try { await busy(event.currentTarget, async () => { const result = await api('initialize',{}); notice(result.created ? 'Database initialized. You can now import components.' : 'The inventory database already exists.'); if (state.settings.client_token_saved) await loadInventory(); }, 'Initializing…'); } catch(error) { showError(error); } });
$('retry-outbox').addEventListener('click', async event => { clearMessages(); try { await busy(event.currentTarget, async () => { await api('flush',{}); await loadRequests(); if (state.settings.client_token_saved) await loadInventory(); }, 'Synchronizing…'); } catch(error) { showError(error); } });
$('sync-server').addEventListener('click', async event => { clearMessages(); try { await busy(event.currentTarget, async () => { await api('sync',{}); await loadQueue(); if (state.settings.client_token_saved) await loadInventory(); }, 'Synchronizing…'); } catch(error) { showError(error); await loadQueue().catch(() => {}); } });
$('error-list').addEventListener('click', async event => {
  const button = event.target.closest('[data-queue-action]'); if (!button) return;
  if (button.dataset.queueAction === 'reject' && !confirm(`Reject request #${button.dataset.number}? Its quantity change will not be applied. Submit a corrected request afterwards.`)) return;
  clearMessages(); try { await busy(button, async () => { await api('queue',{number:Number(button.dataset.number),action:button.dataset.queueAction}); await loadQueue(); }); } catch(error) { showError(error); }
});

setInterval(async () => {
  if (!state.settings || document.hidden || state.loading) return;
  state.loading = true;
  try {
    if (state.settings.mode === 'server') await loadQueue();
    if (state.currentView === 'requests') await loadRequests();
    if (state.currentView === 'inventory' && state.settings.client_token_saved && state.settings.repo) await loadInventory();
  } catch { /* Explicit actions show failures; background refresh keeps last visible state. */ }
  finally { state.loading = false; }
}, 30000);
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
boot();
