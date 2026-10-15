(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const number = (value) => Number.isFinite(Number(value)) ? new Intl.NumberFormat().format(Number(value)) : '—';
  const entryParams = new URLSearchParams(window.location.search);
  const state = { overview: null, projectId: null, threadId: null, messageId: entryParams.get('message_id'), citationThreadId: entryParams.get('thread_id'), citationResolved: false, citationLocated: false, q: '', provider: '', from: '', to: '', origin: 'human', offset: 0, limit: 40, total: 0, request: 0, loading: false };
  let messageController;
  let searchTimer;
  let toastTimer;
  let lastDate = '';

  const icons = {
    codex: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m7 5-5 5 5 5m6-10 5 5-5 5m-2-12-2 14"/></svg>',
    claude: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m10 2 .7 5.5 4-3.8-2.6 4.8 5.7-.8-5.2 2.5 4.7 3.2-5.6-1.5 1.9 5.4-3.6-4.5-1.6 5.5.3-5.7-4.8 3.2 3.3-4.8-5.7.3L7.4 9 3 5.4l5.4 1.9z"/></svg>',
    archive: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M3 4h18v4H3zm6 8h6"/></svg>',
    search: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/></svg>',
    copy: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><rect x="5" y="5" width="8" height="9" rx="1"/><path d="M10 3V2H2v9h1"/></svg>',
    error: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v6m0 3v1"/></svg>'
  };

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  async function api(path, signal) {
    const response = await fetch(path, { signal, headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!response.ok) throw new Error(`The archive could not be read (${response.status}).`);
    return response.json();
  }

  function project() {
    return state.overview?.projects?.find((item) => String(item.id) === String(state.projectId));
  }

  function dateValue(value) {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function dateLabel(value, options = {}) {
    const date = dateValue(value);
    return date ? date.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric', ...options }) : 'Date unavailable';
  }

  function providerName(value) {
    const normalized = String(value || '').toLowerCase();
    return normalized === 'codex' ? 'Codex' : normalized === 'claude' ? 'Claude' : value || 'AI';
  }

  function toast(message) {
    $('toast').textContent = message;
    $('toast').hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $('toast').hidden = true; }, 2600);
  }

  function setSidebar(open) {
    $('sidebar').classList.toggle('open', open);
    $('sidebar').inert = !open && window.matchMedia('(max-width: 760px)').matches;
    $('sidebar-backdrop').hidden = !open;
    $('menu-toggle').setAttribute('aria-expanded', String(open));
    if (!open) $('menu-toggle').focus({ preventScroll: true });
    else $('all-projects').focus({ preventScroll: true });
  }

  function renderProjects() {
    const list = $('project-list');
    list.replaceChildren();
    const query = $('project-search').value.trim().toLocaleLowerCase();
    const projects = state.overview?.projects || [];
    const filtered = projects.filter((item) => `${item.name} ${item.canonical_path || ''}`.toLocaleLowerCase().includes(query));
    $('project-total').textContent = number(projects.length);
    $('sidebar-project-count').textContent = number(projects.length);
    $('all-projects').classList.toggle('active', state.projectId === null);
    if (state.projectId === null) $('all-projects').setAttribute('aria-current', 'page');
    else $('all-projects').removeAttribute('aria-current');
    if (!filtered.length) {
      list.append(element('p', 'sidebar-placeholder', query ? 'No projects match.' : 'Your projects will appear here.'));
      return;
    }
    filtered.forEach((item) => {
      const selected = String(state.projectId) === String(item.id);
      const button = element('button', `project-item${selected ? ' active' : ''}`);
      button.type = 'button';
      button.title = item.canonical_path || item.name;
      if (selected) button.setAttribute('aria-current', 'page');
      button.append(element('span', 'project-dot'), element('span', 'project-name', item.name || 'Unnamed project'), element('span', 'project-count', number(item.message_count)));
      button.addEventListener('click', () => selectProject(item.id));
      list.append(button);
    });
  }

  function renderOverview() {
    const stats = state.overview.stats || {};
    ['messages', 'projects', 'threads', 'profiles'].forEach((key) => { $(`stat-${key}`).textContent = number(stats[key]); });
    const imported = dateValue(stats.last_import_at);
    const importStatus = state.overview.imports?.[0]?.status;
    const incomplete = importStatus === 'partial' || importStatus === 'interrupted';
    const status = importStatus === 'partial' ? 'Partial import' : importStatus === 'interrupted' ? 'Import interrupted' : imported ? `Imported ${dateLabel(stats.last_import_at)}` : stats.messages > 0 ? 'Archive ready' : 'Awaiting first import';
    $('import-status').lastElementChild.textContent = status;
    $('import-status').title = imported ? `Last import: ${imported.toLocaleString()}` : status;
    $('import-status').classList.toggle('incomplete', incomplete);
    const notices = [];
    if (importStatus === 'partial') notices.push('The latest import is partial. Some local history could not be imported, so this archive may be incomplete.');
    if (importStatus === 'interrupted') notices.push('The latest import was interrupted. Your archive includes the messages saved before it stopped and may be incomplete.');
    const missingTimestamps = Number(stats.missing_timestamps) || 0;
    if (missingTimestamps > 0) notices.push(`${number(missingTimestamps)} ${missingTimestamps === 1 ? 'message has' : 'messages have'} no recorded timestamp and ${missingTimestamps === 1 ? 'appears' : 'appear'} after dated messages.`);
    $('archive-notices').replaceChildren(...notices.map((notice) => element('p', '', notice)));
    $('archive-notices').hidden = notices.length === 0;
    const reviewCount = Number(stats.review_messages) || 0;
    $('review-notice').hidden = reviewCount === 0;
    renderReviewNotice();
    const providers = state.overview.providers || [];
    const currentProvider = state.provider;
    const select = $('provider-filter');
    select.replaceChildren(new Option('All AI providers', ''));
    const values = providers.length ? providers : ['codex', 'claude'];
    values.forEach((provider) => {
      const value = typeof provider === 'string' ? provider : provider.id || provider.name;
      if (value) select.add(new Option(providerName(value), value));
    });
    if (Array.from(select.options).some((option) => option.value === currentProvider)) select.value = currentProvider;
    else state.provider = '';
    renderProjects();
    renderScope();
  }

  function renderScope() {
    const selected = project();
    const name = state.origin === 'review' ? 'Needs review' : selected?.name || 'All projects';
    $('breadcrumb-scope').textContent = name;
    $('scope-tag').textContent = name;
    $('scope-tag').title = selected?.canonical_path || name;
    $('scope-description').textContent = state.messageId ? 'A cited message, with the AI reply that came before it.' : state.origin === 'review' ? 'Records whose human authorship needs a closer look.' : state.threadId ? 'One conversation, from your first message onward.' : selected ? 'Every conversation in this project, in the order you sent it.' : 'Your messages, in the order you sent them.';
    $('thread-panel').hidden = !selected;
    $('archive-body').classList.toggle('has-threads', Boolean(selected));
    $('footer-origin').textContent = state.origin === 'review' ? 'Reviewing uncertain records' : 'Human messages only';
    $('citation-notice').hidden = !state.messageId;
    $('show-conversation').disabled = !state.citationResolved;
    $('filter-form').hidden = Boolean(state.messageId);
    if (state.messageId) $('date-filters').hidden = true;
    document.querySelector('.chronology-label').hidden = Boolean(state.messageId);
    renderReviewNotice();
  }

  function clearCitation() {
    if (!state.messageId) return;
    state.messageId = null;
    state.citationThreadId = null;
    state.citationResolved = false;
    state.citationLocated = false;
    const url = new URL(window.location.href);
    url.searchParams.delete('message_id');
    url.searchParams.delete('thread_id');
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
  }

  function showConversation() {
    if (!state.citationResolved) return;
    clearCitation();
    state.offset = 0;
    renderScope();
    loadMessages();
  }

  function renderReviewNotice() {
    const reviewCount = Number(state.overview?.stats?.review_messages) || 0;
    const reviewing = state.origin === 'review';
    $('review-notice').hidden = reviewCount === 0 && !reviewing;
    $('review-description').textContent = reviewing ? 'These records may contain agent-generated or relayed instructions. Their original source is preserved for inspection.' : `${number(reviewCount)} records need source review and are excluded from the human message timeline.`;
    $('review-toggle').textContent = reviewing ? 'Return to human messages →' : 'Review records →';
  }

  async function loadOverview() {
    $('refresh-button').disabled = true;
    $('overview-error').hidden = true;
    try {
      const overview = await api('/api/overview');
      state.overview = overview;
      if (state.projectId !== null && !project()) { state.projectId = null; state.threadId = null; }
      renderOverview();
      if (state.messageId) await loadMessages();
      else await Promise.all([loadThreads(), loadMessages()]);
    } catch (error) {
      $('overview-error').replaceChildren(document.createTextNode('Your archive is unavailable. Check that the local app is running, then '));
      const retry = element('button', '', 'try again');
      retry.type = 'button';
      retry.addEventListener('click', loadOverview);
      $('overview-error').append(retry, document.createTextNode('.'));
      $('overview-error').hidden = false;
      $('import-status').lastElementChild.textContent = 'Could not connect';
      if (!state.overview) {
        $('project-list').replaceChildren(element('p', 'sidebar-placeholder', 'Unable to load projects.'));
        $('messages').replaceChildren(emptyState('Your archive is temporarily unavailable', 'Check that the local app is running, then refresh to reconnect.', 'error'));
        $('messages').setAttribute('aria-busy', 'false');
        $('results-count').textContent = 'Unable to load archive';
      }
    } finally {
      $('refresh-button').disabled = false;
    }
  }

  async function selectProject(id) {
    clearCitation();
    state.origin = 'human';
    state.projectId = id;
    state.threadId = null;
    state.offset = 0;
    renderProjects();
    renderScope();
    if ($('sidebar').classList.contains('open')) setSidebar(false);
    await Promise.all([loadThreads(), loadMessages()]);
  }

  async function loadThreads() {
    const selectedId = state.projectId;
    if (selectedId === null) return;
    $('thread-list').replaceChildren(element('p', 'sidebar-placeholder', 'Loading conversations…'));
    $('thread-count').textContent = '';
    try {
      const data = await api(`/api/projects/${encodeURIComponent(selectedId)}/threads`);
      if (String(state.projectId) !== String(selectedId)) return;
      const threads = data.threads || [];
      $('thread-count').textContent = number(threads.length);
      $('thread-list').replaceChildren();
      renderThreadSelection();
      if (!threads.length) $('thread-list').append(element('p', 'sidebar-placeholder', 'No conversations in this project yet.'));
      threads.forEach((thread) => {
        const button = element('button', `thread-item${String(thread.id) === String(state.threadId) ? ' active' : ''}`);
        button.type = 'button';
        button.dataset.threadId = thread.id;
        button.setAttribute('aria-pressed', String(String(thread.id) === String(state.threadId)));
        button.title = thread.title || 'Untitled conversation';
        const title = element('div', 'thread-title', thread.title || 'Untitled conversation');
        const meta = element('span', 'thread-meta');
        meta.append(element('span', '', providerName(thread.provider)), element('i', '', `${number(thread.message_count)} ${thread.message_count === 1 ? 'message' : 'messages'}`));
        button.append(title, meta);
        button.addEventListener('click', () => {
          clearCitation();
          state.threadId = thread.id;
          state.offset = 0;
          renderThreadSelection();
          renderScope();
          loadMessages();
        });
        $('thread-list').append(button);
      });
    } catch (error) {
      if (String(state.projectId) !== String(selectedId)) return;
      const message = element('p', 'sidebar-placeholder', 'Conversations could not be loaded. ');
      const retry = element('button', 'text-button', 'Retry');
      retry.type = 'button';
      retry.addEventListener('click', loadThreads);
      message.append(retry);
      $('thread-list').replaceChildren(message);
    }
  }

  function renderThreadSelection() {
    $('all-threads').classList.toggle('active', state.threadId === null);
    $('all-threads').setAttribute('aria-pressed', String(state.threadId === null));
    document.querySelectorAll('[data-thread-id]').forEach((button) => {
      const active = String(button.dataset.threadId) === String(state.threadId);
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
  }

  function emptyState(title, description, icon = 'archive', action) {
    const section = element('div', 'empty-state');
    const symbol = element('div', 'empty-symbol');
    symbol.innerHTML = icons[icon] || icons.archive;
    section.append(symbol, element('h3', '', title), element('p', '', description));
    if (action) {
      const button = element('button', 'secondary-button', action.label);
      button.type = 'button';
      button.addEventListener('click', action.handler);
      section.append(button);
    }
    return section;
  }

  async function loadMessages() {
    const requestId = ++state.request;
    messageController?.abort();
    messageController = new AbortController();
    state.loading = true;
    $('messages').setAttribute('aria-busy', 'true');
    $('messages').replaceChildren(element('div', 'message-skeleton'), element('div', 'message-skeleton'));
    $('results-count').textContent = 'Loading messages…';
    $('pagination').hidden = true;
    const params = new URLSearchParams({ limit: state.limit, offset: state.offset, origin: state.origin });
    if (state.messageId) {
      params.set('message_id', state.messageId);
      if (state.citationThreadId) params.set('thread_id', state.citationThreadId);
    } else {
      if (state.projectId !== null) params.set('project_id', state.projectId);
      if (state.threadId !== null) params.set('thread_id', state.threadId);
      if (state.q) params.set('q', state.q);
      if (state.provider) params.set('provider', state.provider);
      if (state.from) params.set('from', state.from);
      if (state.to) params.set('to', state.to);
    }
    try {
      const data = await api(`/api/messages?${params}`, messageController.signal);
      if (requestId !== state.request) return;
      state.total = data.total || 0;
      const messages = data.messages || [];
      if (state.messageId) {
        state.citationResolved = messages.length === 1 && String(messages[0].id) === String(state.messageId);
        if (state.citationResolved) {
          state.projectId = messages[0].project_id;
          state.threadId = messages[0].thread_id;
          renderProjects();
          loadThreads();
        }
        renderScope();
      }
      $('messages').replaceChildren();
      lastDate = '';
      const hasFilters = state.q || state.provider || state.from || state.to;
      if (!messages.length) {
        if (state.messageId) $('messages').append(emptyState('This cited message could not be found', 'It may not be in this archive, or the linked conversation may no longer be available.', 'search', { label: 'Browse the archive', handler: () => selectProject(null) }));
        else if (hasFilters) $('messages').append(emptyState('No words found here', 'Try a different search or clear your filters to see more of your archive.', 'search', { label: 'Clear filters', handler: clearFilters }));
        else if (state.origin === 'review') $('messages').append(emptyState('No records need review', 'Your imported archive has no uncertain records to inspect.'));
        else if (!state.overview?.stats?.messages) $('messages').append(emptyState('Your story starts with an import', 'Once your local Codex and Claude history is imported, your projects and messages will appear here. Every profile is part of the same you.'));
        else $('messages').append(emptyState('No messages in this view', 'Choose another project or conversation to keep exploring your archive.'));
      } else {
        messages.forEach((message) => {
          const date = dateValue(message.sent_at);
          const key = date ? `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}` : 'unknown';
          if (key !== lastDate) {
            const divider = element('div', 'day-divider');
            divider.append(element('span', '', dateLabel(message.sent_at, { weekday: 'short' })));
            $('messages').append(divider);
            lastDate = key;
          }
          $('messages').append(messageCard(message));
        });
      }
      const context = state.threadId ? 'in this conversation' : state.projectId !== null ? 'in this project' : 'across all projects';
      const recordLabel = state.origin === 'review' ? (state.total === 1 ? 'record' : 'records') : (state.total === 1 ? 'message' : 'messages');
      $('results-count').textContent = state.messageId ? state.citationResolved ? 'Cited message · original context' : 'Cited message unavailable' : `${number(state.total)} ${recordLabel} ${hasFilters ? 'matching your filters' : state.origin === 'review' ? 'to review' : context}`;
      $('pagination').hidden = Boolean(state.messageId) || state.total <= state.limit;
      $('previous-page').disabled = state.offset === 0;
      $('next-page').disabled = state.offset + state.limit >= state.total;
      $('page-label').textContent = `${number(state.offset + 1)}–${number(Math.min(state.offset + state.limit, state.total))} of ${number(state.total)}`;
      if (state.messageId && state.citationResolved && !state.citationLocated) {
        state.citationLocated = true;
        document.getElementById(`message-${state.messageId}`)?.scrollIntoView({ block: 'start' });
      }
    } catch (error) {
      if (error.name === 'AbortError' || requestId !== state.request) return;
      $('messages').replaceChildren(emptyState('These messages could not be loaded', 'The archive may still be importing. Try again in a moment.', 'error', { label: 'Try again', handler: loadMessages }));
      $('results-count').textContent = 'Unable to load messages';
    } finally {
      if (requestId === state.request) {
        state.loading = false;
        $('messages').setAttribute('aria-busy', 'false');
      }
    }
  }

  function messageCard(message) {
    const card = element('article', 'message-card');
    const cited = Boolean(state.messageId) && String(state.messageId) === String(message.id);
    card.id = `message-${message.id}`;
    if (cited) card.classList.add('cited-message');
    const header = element('header', 'message-header');
    const provider = String(message.provider || '').toLowerCase();
    const icon = element('span', `provider-icon ${['codex', 'claude'].includes(provider) ? provider : ''}`);
    icon.innerHTML = icons[provider] || icons.codex;
    icon.title = providerName(message.provider);
    const context = element('div', 'message-context');
    const location = element('div', 'message-location');
    const knownProject = state.overview?.projects?.some((item) => String(item.id) === String(message.project_id));
    const projectButton = element(knownProject ? 'button' : 'span', 'message-project-name', message.project_name || 'Unnamed project');
    if (knownProject) {
      projectButton.type = 'button';
      projectButton.addEventListener('click', () => selectProject(message.project_id));
    }
    location.append(projectButton, element('span', '', '·'), element('span', '', providerName(message.provider)));
    context.append(location, element('div', 'message-thread', message.thread_title || 'Untitled conversation'));
    const time = element('time', '', dateValue(message.sent_at)?.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) || '—');
    if (message.sent_at) time.dateTime = message.sent_at;
    time.title = dateValue(message.sent_at)?.toLocaleString(undefined, { dateStyle: 'full', timeStyle: 'long' }) || 'Timestamp unavailable';
    header.append(icon, context, time);
    const text = element('div', 'message-text', message.text || '');
    card.append(header, previousReply(message), text);
    if (!cited && ((message.text || '').length > 1400 || (message.text || '').split('\n').length > 12)) {
      text.classList.add('collapsed');
      const expand = element('button', 'message-expand', 'Read full message ↓');
      expand.type = 'button';
      expand.setAttribute('aria-expanded', 'false');
      expand.addEventListener('click', () => {
        const collapsed = text.classList.toggle('collapsed');
        expand.textContent = collapsed ? 'Read full message ↓' : 'Show less ↑';
        expand.setAttribute('aria-expanded', String(!collapsed));
      });
      card.append(expand);
    }
    const footer = element('footer', 'message-footer');
    const attribution = element('span', 'message-attribution');
    const profiles = message.profile_names || [];
    const isReview = state.origin === 'review';
    const profileText = isReview ? `Needs review${profiles.length ? ` · ${profiles.join(', ')}` : ''}` : profiles.length ? `You · ${profiles.join(', ')}` : 'You · Human message';
    attribution.append(element('i', 'human-dot'), element('span', '', profileText));
    attribution.title = profileText;
    const actions = element('div', 'message-actions');
    const sources = Array.isArray(message.provenance) ? Array.from(new Map(message.provenance.map((source) => [`${source.path || source.file_path || ''}:${source.line || ''}:${source.source_sha256 || ''}`, source])).values()) : [];
    let details;
    if (sources.length) {
      details = element('details', 'source-details');
      const sourceCount = sources.length;
      const summary = element('summary', '', `Original ${sourceCount === 1 ? 'source' : `${number(sourceCount)} sources`}`);
      const list = element('ul');
      sources.forEach((source) => {
        const row = element('li');
        row.append(element('code', '', `${source.path || source.file_path || 'Source file'}${source.line ? `:${source.line}` : ''}`));
        if (source.is_current === 0 || source.is_current === false) {
          const revision = element('span', 'source-revision', 'Earlier source revision');
          revision.title = 'This location refers to the imported version of the file. The current file has changed.';
          row.append(document.createTextNode(' '), revision);
        }
        list.append(row);
      });
      details.append(summary, list);
    }
    const copy = element('button');
    copy.type = 'button';
    copy.setAttribute('aria-label', 'Copy message text');
    copy.innerHTML = `${icons.copy}<span>Copy</span>`;
    copy.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(message.text || '');
        toast('Message copied');
      } catch (error) {
        toast('Copy is unavailable. Select the message text to copy it.');
      }
    });
    actions.append(copy);
    if (!isReview) {
      const copyLink = element('button', '', 'Copy link');
      copyLink.type = 'button';
      copyLink.setAttribute('aria-label', 'Copy link to this message and its previous AI reply');
      copyLink.addEventListener('click', async () => {
        const url = new URL('/', window.location.origin);
        url.searchParams.set('message_id', message.id);
        url.searchParams.set('thread_id', message.thread_id);
        try {
          await navigator.clipboard.writeText(url.href);
          toast('Message link copied');
        } catch (error) {
          toast('Copy is unavailable in this browser.');
        }
      });
      actions.append(copyLink);
    }
    footer.append(attribution, actions);
    card.append(footer);
    if (isReview && message.classification_reason) card.append(element('p', 'message-review-reason', `Review reason: ${message.classification_reason}`));
    if (details) card.append(details);
    return card;
  }

  function previousReply(message) {
    const reply = message.previous_reply;
    if (!reply || typeof reply.text !== 'string' || !reply.text.trim()) {
      const unavailable = element('p', 'previous-reply-unavailable', 'No previous final reply in available history');
      const status = message.previous_reply_status;
      const labels = {
        first_message: 'First message · no previous AI reply',
        no_previous_agent_reply: 'No earlier AI reply in this thread',
        previous_turn_without_final_reply: 'Previous turn has no recorded final reply',
        previous_turn_interrupted: 'Previous turn interrupted · no final reply',
        previous_turn_incomplete: 'Previous turn has no recorded final reply',
        previous_turn_failed: 'Previous turn failed · no final reply',
        previous_reply_has_no_text: 'Previous AI reply has no recorded text',
        not_yet_scanned: 'Previous AI reply has not been scanned yet',
      };
      if (labels[status]) unavailable.textContent = labels[status];
      if (status === 'history_only' || status === 'not_in_available_history') unavailable.title = 'The available source history does not include the preceding final AI reply.';
      else if (status === 'no_final_reply') unavailable.title = 'No completed AI reply was recorded before this message.';
      return unavailable;
    }

    const details = element('details', 'previous-reply');
    if (state.messageId && String(state.messageId) === String(message.id)) details.open = true;
    const summary = element('summary', 'previous-reply-toggle');
    const chevron = element('span', 'previous-reply-chevron');
    chevron.innerHTML = '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m6 3 5 5-5 5"/></svg>';
    const reason = String(reply.selection_reason || '').toLowerCase();
    const completed = ['claude_end_turn', 'claude_stop_sequence', 'claude_success_result', 'claude_stop_hook_completed', 'claude_turn_completed', 'task_complete_last_agent_message'].includes(reason);
    const inferred = /infer|legacy|before|fallback|boundary/.test(reason);
    const label = inferred ? 'Last reply before your message' : completed || /end.of.turn|turn.completed|turn.complete|stop.reason/.test(reason) ? 'End-of-turn reply' : /final/.test(reason) ? 'Final reply' : 'Last reply before your message';
    summary.append(chevron, element('span', 'previous-reply-label', 'Previous AI reply'), element('span', 'previous-reply-kind', label));

    const body = element('div', 'previous-reply-body');
    const meta = element('div', 'previous-reply-meta');
    meta.append(element('span', 'previous-reply-provider', providerName(reply.provider || message.provider)));
    const date = dateValue(reply.sent_at);
    const time = element('time', '', date ? date.toLocaleString(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : 'Timestamp unavailable');
    if (reply.sent_at) time.dateTime = reply.sent_at;
    if (date) time.title = date.toLocaleString(undefined, { dateStyle: 'full', timeStyle: 'long' });
    meta.append(time);
    body.append(meta, element('div', 'previous-reply-text', reply.text));

    const sources = Array.isArray(reply.provenance) ? Array.from(new Map(reply.provenance.map((source) => [`${source.path || source.file_path || ''}:${source.line || ''}:${source.source_sha256 || ''}`, source])).values()) : [];
    if (sources.length) {
      const sourceDetails = element('details', 'previous-reply-sources');
      const sourceSummary = element('summary', '', `AI reply ${sources.length === 1 ? 'source' : `sources (${number(sources.length)})`}`);
      const list = element('ul');
      sources.forEach((source) => {
        const row = element('li');
        row.append(element('code', '', `${source.path || source.file_path || 'Source file'}${source.line ? `:${source.line}` : ''}`));
        if (source.is_current === 0 || source.is_current === false) row.append(document.createTextNode(' '), element('span', 'source-revision', 'Earlier source revision'));
        list.append(row);
      });
      sourceDetails.append(sourceSummary, list);
      body.append(sourceDetails);
    }

    details.append(summary, body);
    return details;
  }

  function clearFilters() {
    state.q = ''; state.provider = ''; state.from = ''; state.to = ''; state.offset = 0;
    $('message-search').value = '';
    $('provider-filter').value = '';
    $('date-from').value = '';
    $('date-to').value = '';
    $('date-toggle').classList.remove('active');
    loadMessages();
  }

  async function showSources() {
    const dialog = $('sources-dialog');
    $('sources-content').replaceChildren(element('p', 'dialog-intro', 'Reading import sources…'));
    dialog.showModal();
    try {
      const data = await api('/api/sources');
      const profiles = data.profiles || [];
      const files = data.files || [];
      const summary = element('div', 'source-summary');
      const values = [[profiles.length, 'profiles'], [data.summary?.files ?? data.summary?.source_files ?? files.length, 'source files'], [state.overview?.stats?.messages ?? 0, 'human messages']];
      values.forEach(([count, label]) => {
        const item = element('div');
        item.append(element('strong', '', number(count)), document.createTextNode(label));
        summary.append(item);
      });
      $('sources-content').replaceChildren(summary);
      profiles.forEach((profile) => {
        const item = element('div', 'source-profile');
        item.append(element('strong', '', profile.name || profile.profile_name || 'Local profile'), element('span', '', providerName(profile.provider)));
        if (profile.path || profile.root_path || profile.profile_path) item.append(element('code', '', profile.path || profile.root_path || profile.profile_path));
        $('sources-content').append(item);
      });
      if (!profiles.length) $('sources-content').append(element('p', 'dialog-intro', 'No profiles have been imported yet. Import your local history, then refresh your archive.'));
      const reviewCount = state.overview?.stats?.review_messages || 0;
      if (reviewCount > 0) $('sources-content').append(element('p', 'source-review-note', `${number(reviewCount)} records need source review and are excluded from this human message timeline.`));
    } catch (error) {
      $('sources-content').replaceChildren(element('p', 'dialog-intro', 'Import source details could not be loaded. Close this window and try again.'));
    }
  }

  const citationNotice = element('div', 'citation-notice');
  citationNotice.id = 'citation-notice';
  citationNotice.hidden = !state.messageId;
  const citationDescription = element('div');
  citationDescription.append(element('strong', '', 'Message evidence'), element('p', '', 'The original message and its preceding AI reply, kept together for context.'));
  const conversationButton = element('button', 'secondary-button', 'Show full conversation');
  conversationButton.id = 'show-conversation';
  conversationButton.type = 'button';
  conversationButton.disabled = true;
  conversationButton.addEventListener('click', showConversation);
  citationNotice.append(citationDescription, conversationButton);
  $('filter-form').before(citationNotice);

  $('all-projects').addEventListener('click', () => selectProject(null));
  $('project-search').addEventListener('input', renderProjects);
  $('refresh-button').addEventListener('click', loadOverview);
  $('filter-form').addEventListener('submit', (event) => { event.preventDefault(); clearTimeout(searchTimer); state.q = $('message-search').value.trim(); state.offset = 0; loadMessages(); });
  $('message-search').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.q = $('message-search').value.trim(); state.offset = 0; loadMessages(); }, 280);
  });
  $('provider-filter').addEventListener('change', () => { state.provider = $('provider-filter').value; state.offset = 0; loadMessages(); });
  $('all-threads').addEventListener('click', () => { clearCitation(); state.threadId = null; state.offset = 0; renderThreadSelection(); renderScope(); loadMessages(); });
  $('date-toggle').addEventListener('click', () => {
    const open = $('date-filters').hidden;
    $('date-filters').hidden = !open;
    $('date-toggle').setAttribute('aria-expanded', String(open));
  });
  function updateDates() {
    const from = $('date-from').value;
    const to = $('date-to').value;
    $('date-to').min = from;
    $('date-from').max = to;
    if (from && to && from > to) { toast('The end date must come after the start date.'); return; }
    state.from = from; state.to = to; state.offset = 0;
    $('date-toggle').classList.toggle('active', Boolean(from || to));
    loadMessages();
  }
  $('date-from').addEventListener('change', updateDates);
  $('date-to').addEventListener('change', updateDates);
  $('clear-dates').addEventListener('click', () => { $('date-from').value = ''; $('date-to').value = ''; updateDates(); });
  function paginate(delta) {
    state.offset = Math.max(0, state.offset + delta * state.limit);
    loadMessages();
    $('archive-title').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  $('previous-page').addEventListener('click', () => paginate(-1));
  $('next-page').addEventListener('click', () => paginate(1));
  $('menu-toggle').addEventListener('click', () => setSidebar(!$('sidebar').classList.contains('open')));
  $('sidebar-backdrop').addEventListener('click', () => setSidebar(false));
  $('sources-button').addEventListener('click', showSources);
  $('review-toggle').addEventListener('click', () => {
    clearCitation();
    state.origin = state.origin === 'review' ? 'human' : 'review';
    state.projectId = null; state.threadId = null; state.offset = 0;
    renderProjects(); renderScope(); loadMessages();
  });
  document.querySelector('.dialog-close').addEventListener('click', () => $('sources-dialog').close());
  $('sources-dialog').addEventListener('click', (event) => { if (event.target === $('sources-dialog')) { const rect = $('sources-dialog').getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) $('sources-dialog').close(); } });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && $('sidebar').classList.contains('open')) setSidebar(false);
    const editing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '') || document.activeElement?.isContentEditable;
    if (event.key === '/' && !editing && !$('sources-dialog').open && !event.ctrlKey && !event.metaKey && !event.altKey) { event.preventDefault(); $('message-search').focus(); }
    if (event.key === 'Tab' && $('sidebar').classList.contains('open')) {
      const focusable = Array.from($('sidebar').querySelectorAll('a,button,input')).filter((node) => !node.disabled && node.offsetParent !== null);
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  });

  const mobileLayout = window.matchMedia('(max-width: 760px)');
  function syncSidebarAccessibility() {
    $('sidebar').inert = mobileLayout.matches && !$('sidebar').classList.contains('open');
    if (!mobileLayout.matches) { $('sidebar').classList.remove('open'); $('sidebar-backdrop').hidden = true; $('menu-toggle').setAttribute('aria-expanded', 'false'); }
  }
  mobileLayout.addEventListener('change', syncSidebarAccessibility);
  syncSidebarAccessibility();
  document.querySelector('.chronology-label').title = `Timestamps are shown in your local time zone: ${Intl.DateTimeFormat().resolvedOptions().timeZone}. Date filters use UTC dates.`;
  loadOverview();
})();
