'use strict';

var STATE = { result: null, rating: null, exportText: '', exportName: 'plan.md' };

function el(id) { return document.getElementById(id); }

function esc(value) {
  if (value === null || value === undefined) { return ''; }
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function clock(minutes) {
  if (minutes === null || minutes === undefined) { return '--:--'; }
  var value = Math.round(minutes);
  var hour = Math.floor(value / 60) % 24;
  var min = value % 60;
  return (hour < 10 ? '0' + hour : '' + hour) + ':' + (min < 10 ? '0' + min : '' + min);
}

function mins(value) {
  if (value === null || value === undefined) { return '--'; }
  var n = Math.round(value);
  if (Math.abs(n) < 60) { return n + 'm'; }
  var h = Math.floor(n / 60);
  var m = n % 60;
  return h + 'h' + (m < 10 ? '0' + m : '' + m) + 'm';
}

function setStatus(text, kind) {
  var node = el('status');
  node.textContent = text || '';
  node.className = 'status' + (kind ? ' ' + kind : '');
}

function post(url, payload) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }).then(function (response) {
    return response.json().then(function (data) {
      if (!response.ok) { throw new Error(data.error || 'request failed'); }
      return data;
    });
  });
}

function loadSamples() {
  fetch('/api/samples').then(function (response) { return response.json(); })
    .then(function (data) {
      var box = el('samples');
      box.innerHTML = '';
      (data.samples || []).forEach(function (sample) {
        var button = document.createElement('button');
        button.textContent = sample.name.replace('.txt', '');
        button.title = sample.title;
        button.onclick = function () {
          el('brief').value = sample.brief;
          runAnalyze();
        };
        box.appendChild(button);
      });
      if (data.samples && data.samples.length) {
        el('brief').value = data.samples[0].brief;
      }
    }).catch(function (error) { setStatus(error.message, 'error'); });
}

function scoreClass(score) {
  if (score >= 78) { return 'good'; }
  if (score >= 55) { return 'warn'; }
  return 'bad';
}

function render(result) {
  STATE.result = result;
  var metrics = result.metrics;
  var score = result.score;
  var html = '';

  html += '<div class="score-head">';
  var ringColor = score.overall >= 78 ? 'var(--good)' : (score.overall >= 55 ? 'var(--warn)' : 'var(--bad)');
  html += '<div class="score-ring" style="border-color:' + ringColor + '">' + score.overall + '<small>' + esc(score.verdict) + '</small></div>';
  html += '<div><div class="verdict">' + esc(score.verdict) + '</div><div class="verdict-note">' + esc(score.verdict_note) + '</div></div>';
  html += '<div class="chips">';
  html += chip('Jobs', metrics.jobs, '');
  html += chip('Crewed', metrics.assigned, metrics.unassigned ? 'warn' : 'good');
  html += chip('On time', metrics.on_time + '/' + metrics.assigned, metrics.late_jobs ? 'bad' : 'good');
  html += chip('Late', mins(metrics.late_minutes), metrics.late_minutes ? 'bad' : 'good');
  html += chip('Driving', mins(metrics.travel_minutes), '');
  html += chip('Idle', mins(metrics.idle_minutes), '');
  html += chip('Overtime', mins(metrics.overtime_minutes), metrics.overtime_minutes ? 'warn' : 'good');
  html += chip('Coverage', Math.round(metrics.coverage_pct) + '%', metrics.coverage_pct < 100 ? 'warn' : 'good');
  html += '</div></div>';

  html += '<section class="block"><h3>Scorecard</h3>';
  score.axes.forEach(function (axis) {
    html += '<div class="axis"><span>' + esc(axis.label) + '</span><span class="bar ' + scoreClass(axis.score) + '"><span style="width:' + Math.max(0, Math.min(100, axis.score)) + '%"></span></span><span class="num">' + axis.score + '</span></div>';
    html += '<div class="axis-note">' + esc(axis.note) + '</div>';
  });
  if (score.caps.length) {
    html += '<ul class="caps">';
    score.caps.forEach(function (cap) {
      html += '<li>' + esc(cap.axis) + ' capped at ' + cap.capped_at + ' (' + esc(cap.reason) + ')</li>';
    });
    html += '</ul>';
  }
  html += '</section>';

  html += '<section class="block"><h3>Routes</h3>';
  (result.schedule.routes || []).forEach(function (route) {
    html += renderRoute(route);
  });
  html += '</section>';

  html += '<section class="block"><h3>Job board</h3><table><thead><tr>' +
    '<th>Job</th><th>Customer</th><th>Priority</th><th>Window</th><th>Crew</th>' +
    '<th class="num">Arrive</th><th class="num">Late</th><th class="num">Idle</th><th>Status</th></tr></thead><tbody>';
  (result.jobs_view || []).forEach(function (job) {
    var status = job.status === 'on_time' ? 'on time' : (job.status === 'late' ? 'late' : 'no crew');
    html += '<tr class="' + esc(job.status) + '"><td>' + esc(job.id) + '</td><td>' + esc(job.customer || '-') +
      '</td><td>' + esc(job.priority) + '</td><td>' + clock(job.window_start) + ' - ' + clock(job.window_end) +
      '</td><td>' + esc(job.resource_name || '-') + '</td><td class="num">' + clock(job.start) +
      '</td><td class="num">' + mins(job.late_minutes) + '</td><td class="num">' + mins(job.idle_minutes) +
      '</td><td>' + status + '</td></tr>';
  });
  html += '</tbody></table></section>';

  html += '<section class="block"><h3>Findings (' + (result.findings || []).length + ')</h3>';
  (result.findings || []).forEach(function (item) {
    html += '<div class="finding ' + esc(item.severity) + '"><h4><span class="sev">' + esc(item.severity) +
      '</span>' + esc(item.title) + '</h4><p class="evidence">' + esc(item.evidence) +
      '</p><p class="why">' + esc(item.why) + '</p><p class="fix">' + esc(item.fix) + '</p></div>';
  });
  if (!(result.findings || []).length) {
    html += '<p class="hint">Nothing flagged. The plan is clean for this brief.</p>';
  }
  html += '</section>';

  html += '<section class="block"><h3>If one resource calls in sick</h3><table><thead><tr><th>Resource</th>' +
    '<th class="num">Jobs uncrewed</th><th class="num">Extra lateness</th><th class="num">Impact</th></tr></thead><tbody>';
  (result.resilience || []).forEach(function (item) {
    html += '<tr><td>' + esc(item.name || item.resource) + '</td><td class="num">' + item.unassigned +
      '</td><td class="num">' + mins(item.late_minutes) + '</td><td class="num">' + item.impact_pct + '%</td></tr>';
  });
  html += '</tbody></table></section>';

  html += renderWhatIf();

  html += '<section class="block"><h3>Save this plan</h3><div class="rating">';
  [1, 2, 3, 4, 5].forEach(function (value) {
    html += '<button data-rating="' + value + '">' + value + '</button>';
  });
  html += '</div><input type="text" id="review-notes" placeholder="What was wrong with this plan?"><div class="controls"><button id="save-review">Save review</button></div></section>';

  el('results').innerHTML = html;

  var buttons = document.querySelectorAll('.rating button');
  Array.prototype.forEach.call(buttons, function (button) {
    button.onclick = function () {
      STATE.rating = parseInt(button.getAttribute('data-rating'), 10);
      Array.prototype.forEach.call(buttons, function (other) { other.classList.remove('on'); });
      button.classList.add('on');
    };
  });
  el('save-review').onclick = saveReview;
  bindWhatIf();
}

function chip(label, value, kind) {
  return '<span class="chip ' + kind + '">' + esc(label) + ' <b>' + esc(value) + '</b></span>';
}

function renderRoute(route) {
  var resource = route.resource;
  var totals = route.totals;
  var html = '<div class="route-card"><div class="route-head"><div><strong>' +
    esc(resource.name || resource.id) + '</strong> <span class="route-meta">' +
    esc(resource.id) + ' | ' + clock(resource.shift_start) + ' - ' + clock(resource.shift_end) +
    ' | ' + esc((resource.skills || []).join(', ') || 'no skills listed') + '</span></div>' +
    '<div class="route-meta">' + totals.jobs + ' jobs | ' + mins(totals.travel_minutes) +
    ' driving | ' + mins(totals.service_minutes) + ' on site | ' + mins(totals.late_minutes) +
    ' late | ' + Math.round(totals.utilization * 100) + '% utilised</div></div>';

  if (!route.entries.length) {
    html += '<p class="hint">No jobs assigned to this resource.</p></div>';
    return html;
  }

  var span = Math.max(1, resource.shift_end - resource.shift_start);
  html += '<div class="timeline">';
  route.entries.forEach(function (entry) {
    var left = ((entry.start - resource.shift_start) / span) * 100;
    var width = Math.max(2.5, ((entry.end - entry.start) / span) * 100);
    left = Math.max(0, Math.min(98, left));
    width = Math.min(100 - left, width);
    html += '<div class="block ' + (entry.on_time ? 'on-time' : 'late') + '" style="left:' + left +
      '%;width:' + width + '%" title="' + esc(entry.job_id) + ' ' + clock(entry.start) + '">' + esc(entry.job_id) + '</div>';
  });
  html += '</div>';

  html += '<table><thead><tr><th>#</th><th>Job</th><th class="num">Arrive</th><th>Window</th>' +
    '<th class="num">Late</th><th class="num">Idle</th><th class="num">Leg</th><th>Customer</th></tr></thead><tbody>';
  route.entries.forEach(function (entry, index) {
    html += '<tr class="' + (entry.on_time ? '' : 'late') + '"><td>' + (index + 1) + '</td><td>' +
      esc(entry.job_id) + '</td><td class="num">' + clock(entry.start) + '</td><td>' +
      clock(entry.window_start) + ' - ' + clock(entry.window_end) + '</td><td class="num">' +
      mins(entry.late_minutes) + '</td><td class="num">' + mins(entry.idle_minutes) +
      '</td><td class="num">' + mins(entry.leg_minutes) + '</td><td>' + esc(entry.customer || '-') + '</td></tr>';
  });
  html += '</tbody></table></div>';
  return html;
}

function renderWhatIf() {
  var routes = (STATE.result && STATE.result.schedule.routes) || [];
  var jobs = (STATE.result && STATE.result.jobs_view) || [];
  var html = '<section class="block"><h3>What if</h3><p class="hint">Change one thing and see what the day does.</p><div class="whatif-grid">';
  html += '<button data-scenario="add_resource">Add a second crew</button>';
  html += '<button data-scenario="extend_shift">Extend every shift by 60m</button>';
  html += '<button data-scenario="drop_last">Drop the lowest priority job</button>';
  html += '<button data-scenario="speed_up">Assume 45 mph instead of ' + esc(STATE.result.brief.settings.speed_mph) + '</button>';
  html += '</div><div class="delta" id="delta"></div></section>';
  return html;
}

function bindWhatIf() {
  var buttons = document.querySelectorAll('[data-scenario]');
  Array.prototype.forEach.call(buttons, function (button) {
    button.onclick = function () { runScenario(button.getAttribute('data-scenario')); };
  });
}

function runScenario(kind) {
  var result = STATE.result;
  var scenario = null;
  if (kind === 'add_resource') {
    var existing = (result.schedule.routes || [])[0];
    scenario = {
      op: 'add_resource',
      resource: {
        id: 'helper',
        name: 'Helper crew',
        skills: (existing && existing.resource.skills || []).join(', '),
        shift: clock(result.brief.settings.shift_start) + '-' + clock(result.brief.settings.shift_end)
      }
    };
  } else if (kind === 'extend_shift') {
    scenario = { op: 'extend_shift', minutes: 60 };
  } else if (kind === 'drop_last') {
    var low = null;
    (result.jobs_view || []).forEach(function (job) {
      if (job.status !== 'unassigned' && (!low || job.priority === 'low')) { low = job; }
    });
    if (!low) { setStatus('no job to drop', 'error'); return; }
    scenario = { op: 'remove_job', job_id: low.id };
  } else if (kind === 'speed_up') {
    scenario = { op: 'set_speed', speed_mph: 45 };
  }
  setStatus('running what-if...', 'busy');
  post('/api/scenario', { brief: el('brief').value, scenario: scenario, profile: el('profile').value })
    .then(function (data) {
      setStatus('', null);
      var box = el('delta');
      if (!box) { return; }
      var html = '<table><thead><tr><th>Metric</th><th class="num">Before</th><th class="num">After</th><th class="num">Change</th></tr></thead><tbody>';
      (data.delta || []).forEach(function (row) {
        var cls = row.delta > 0 ? 'up' : (row.delta < 0 ? 'down' : '');
        var sign = row.delta > 0 ? '+' : '';
        html += '<tr><td>' + esc(row.metric) + '</td><td class="num">' + row.before +
          '</td><td class="num">' + row.after + '</td><td class="num ' + cls + '">' + sign + row.delta + '</td></tr>';
      });
      html += '</tbody></table><p class="hint">Score ' + data.before.score.overall + ' -> ' + data.after.score.overall +
        ' (' + (data.score_delta > 0 ? '+' : '') + data.score_delta + ')</p>';
      box.innerHTML = html;
    }).catch(function (error) { setStatus(error.message, 'error'); });
}

function runAnalyze() {
  setStatus('planning...', 'busy');
  post('/api/analyze', { brief: el('brief').value, profile: el('profile').value })
    .then(function (result) {
      setStatus('planned ' + result.metrics.jobs + ' jobs', null);
      render(result);
    })
    .catch(function (error) { setStatus(error.message, 'error'); });
}

function saveReview() {
  if (!STATE.result) { setStatus('nothing to save yet', 'error'); return; }
  var notes = el('review-notes') ? el('review-notes').value : '';
  post('/api/reviews', {
    brief: el('brief').value,
    result: STATE.result,
    rating: STATE.rating,
    notes: notes
  }).then(function () {
    setStatus('review saved', null);
    loadHistory();
  }).catch(function (error) { setStatus(error.message, 'error'); });
}

function loadHistory() {
  fetch('/api/reviews').then(function (response) { return response.json(); })
    .then(function (data) {
      var box = el('history');
      if (!data.reviews || !data.reviews.length) {
        box.innerHTML = '<p class="hint">No saved reviews yet.</p>';
        return;
      }
      var html = '';
      data.reviews.forEach(function (row) {
        html += '<div class="history-item"><span>' + esc(row.created_at.slice(0, 16).replace('T', ' ')) +
          ' | ' + esc(row.verdict) + ' ' + row.overall + (row.rating ? ' | ' + row.rating + '/5' : '') +
          '</span><span class="hint">' + esc((row.notes || '').slice(0, 60)) + '</span></div>';
      });
      box.innerHTML = html;
    }).catch(function () { /* history is optional */ });
}

function exportFile(format) {
  setStatus('building export...', 'busy');
  fetch('/api/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ brief: el('brief').value, format: format, profile: el('profile').value })
  }).then(function (response) { return response.text(); })
    .then(function (text) {
      setStatus('', null);
      STATE.exportText = text;
      STATE.exportName = format === 'csv' ? 'dispatch-plan.csv' : 'dispatch-plan.md';
      el('overlay-title').textContent = 'Export: ' + STATE.exportName;
      el('overlay-body').textContent = text;
      el('overlay').classList.remove('hidden');
    }).catch(function (error) { setStatus(error.message, 'error'); });
}

el('analyze').onclick = runAnalyze;
el('profile').onchange = function () { if (STATE.result) { runAnalyze(); } };
el('export-md').onclick = function () { exportFile('markdown'); };
el('export-csv').onclick = function () { exportFile('csv'); };
el('overlay-close').onclick = function () { el('overlay').classList.add('hidden'); };
el('overlay-download').onclick = function () {
  var blob = new Blob([STATE.exportText], { type: 'text/plain' });
  var link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = STATE.exportName;
  link.click();
  URL.revokeObjectURL(link.href);
};

loadSamples();
loadHistory();
