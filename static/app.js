const state = {
  view: 'workbench',
  summary: {},
  auth: {},
  knowledge: [],
  tasks: [],
  workbenchCandidates: [],
  candidates: [],
  selectedTaskId: '',
  selectedKnowledgeIds: new Set(),
  selectedCandidateIds: new Set(),
  editingKnowledgeId: '',
  taskSubjectFilter: '',
  taskGradeFilter: '',
  taskKnowledgeSearch: '',
  knowledgeSubjectFilter: '',
  knowledgeGradeFilter: '',
  knowledgeSearch: '',
  candidateTaskFilter: 'all',
  candidateSearch: '',
  importPreview: null,
  importMapping: {},
}

const $ = (id) => document.getElementById(id)

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, options)
  const text = await response.text()
  const data = text ? JSON.parse(text) : null
  if (!response.ok) {
    throw new Error(data?.detail || data?.message || response.statusText)
  }
  return data
}

function toast(message) {
  const el = $('toast')
  el.textContent = message
  el.classList.add('show')
  window.setTimeout(() => el.classList.remove('show'), 2600)
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[char]))
}

function formatDuration(seconds) {
  if (!seconds) return '-'
  const total = Number(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${m}:${String(s).padStart(2, '0')}`
}

function formatNumber(value) {
  const number = Number(value || 0)
  if (number >= 10000) return `${(number / 10000).toFixed(1)}万`
  return String(number)
}

function badge(status) {
  if (status === 'COMPLETED') return '<span class="badge done">完成</span>'
  if (status === 'FAILED') return '<span class="badge fail">失败</span>'
  if (status === 'RUNNING' || status === 'DOWNLOADING' || status === 'PARTIAL') return '<span class="badge work">进行中</span>'
  return `<span class="badge">${escapeHtml(status || '-')}</span>`
}

function setView(view) {
  if (state.view !== view) state.selectedCandidateIds.clear()
  state.view = view
  document.querySelectorAll('.view').forEach((el) => el.classList.toggle('active', el.id === `view-${view}`))
  document.querySelectorAll('.nav-btn').forEach((button) => {
    button.classList.toggle('active', button.dataset.view === view)
  })
  $('viewTitle').textContent = {
    workbench: '粗筛下载',
    knowledge: '知识点管理',
    tasks: '任务管理',
    candidates: '候选库管理',
  }[view] || '粗筛下载'
  renderAll()
  if (view === 'candidates') refreshCandidateLibrary().catch((error) => toast(error.message))
}

function uniqueValues(rows, key) {
  return Array.from(new Set(rows.map((item) => item[key]).filter(Boolean))).sort((a, b) => String(a).localeCompare(String(b), 'zh-CN'))
}

function selectOptions(values, selected, allLabel) {
  return [
    `<option value="">${escapeHtml(allLabel)}</option>`,
    ...values.map((value) => `<option value="${escapeHtml(value)}" ${value === selected ? 'selected' : ''}>${escapeHtml(value)}</option>`),
  ].join('')
}

function knowledgeText(item) {
  return [item.subject, item.stage, item.grade, item.textbook, item.chapter, item.group, item.name, ...(item.aliases || [])]
    .filter(Boolean)
    .join(' ')
}

function knowledgeLabel(item, compact = false) {
  if (compact) {
    return [item.chapter, item.group, item.name].filter(Boolean).join(' / ')
  }
  return [item.subject, item.grade, item.chapter, item.group, item.name].filter(Boolean).join(' / ')
}

function matchesText(text, keyword) {
  return !keyword || String(text || '').toLowerCase().includes(keyword.toLowerCase())
}

function filteredTaskKnowledge() {
  return state.knowledge.filter((item) => {
    if (state.taskSubjectFilter && item.subject !== state.taskSubjectFilter) return false
    if (state.taskGradeFilter && item.grade !== state.taskGradeFilter) return false
    return matchesText(knowledgeText(item), state.taskKnowledgeSearch)
  })
}

function filteredKnowledgeManagerRows() {
  return state.knowledge.filter((item) => {
    if (state.knowledgeSubjectFilter && item.subject !== state.knowledgeSubjectFilter) return false
    if (state.knowledgeGradeFilter && item.grade !== state.knowledgeGradeFilter) return false
    return matchesText(knowledgeText(item), state.knowledgeSearch)
  })
}

function filteredCandidateRows() {
  return state.candidates.filter((item) => {
    const text = [item.title, item.author, item.knowledge_name, item.knowledge_chapter, item.knowledge_group, item.task_keyword]
      .filter(Boolean)
      .join(' ')
    return matchesText(text, state.candidateSearch)
  })
}

function renderSummary() {
  $('statKnowledge').textContent = state.summary.knowledge_count || 0
  $('statTasks').textContent = state.summary.task_count || 0
  $('statCandidates').textContent = state.summary.candidate_count || 0
  $('statDownloading').textContent = state.summary.downloading_count || 0
}

function renderAuth() {
  const auth = state.auth || {}
  $('authText').textContent = auth.authorized ? '授权有效' : '未授权'
  $('authDetail').textContent = auth.message || auth.live_message || '等待检测登录态'
}

function renderTaskFilters() {
  $('taskSubjectFilter').innerHTML = selectOptions(uniqueValues(state.knowledge, 'subject'), state.taskSubjectFilter, '全部学科')
  $('taskGradeFilter').innerHTML = selectOptions(uniqueValues(state.knowledge, 'grade'), state.taskGradeFilter, '全部年级')
  $('taskKnowledgeSearch').value = state.taskKnowledgeSearch

  const rows = filteredTaskKnowledge()
  if (!rows.some((item) => item.id === $('knowledgeSelect').value)) {
    $('knowledgeSelect').value = rows[0]?.id || ''
  }
  const selected = $('knowledgeSelect').value
  $('knowledgeSelect').innerHTML = rows.length
    ? rows.map((item) => `<option value="${escapeHtml(item.id)}" ${item.id === selected ? 'selected' : ''}>${escapeHtml(knowledgeLabel(item, true))}</option>`).join('')
    : '<option value="">没有匹配的知识点</option>'
}

function renderKnowledgeManager() {
  $('knowledgeSubjectFilter').innerHTML = selectOptions(uniqueValues(state.knowledge, 'subject'), state.knowledgeSubjectFilter, '全部学科')
  $('knowledgeGradeFilter').innerHTML = selectOptions(uniqueValues(state.knowledge, 'grade'), state.knowledgeGradeFilter, '全部年级')
  $('knowledgeSearch').value = state.knowledgeSearch
  const rows = filteredKnowledgeManagerRows()
  state.selectedKnowledgeIds = new Set(Array.from(state.selectedKnowledgeIds).filter((id) => (
    rows.some((item) => item.id === id)
  )))
  $('knowledgeSelectAll').checked = rows.length > 0 && rows.every((item) => state.selectedKnowledgeIds.has(item.id))
  const body = $('knowledgeBody')
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty">暂无知识点</td></tr>'
    return
  }
  body.innerHTML = rows.map((item) => `
    <tr>
      <td class="check"><input class="knowledge-check" type="checkbox" data-id="${escapeHtml(item.id)}" ${state.selectedKnowledgeIds.has(item.id) ? 'checked' : ''} /></td>
      <td>
        <div class="title">
          <strong>${escapeHtml([item.group, item.name].filter(Boolean).join(' / '))}</strong>
          <small>${escapeHtml(item.chapter || '-')}</small>
        </div>
      </td>
      <td>${escapeHtml(item.subject || '-')}</td>
      <td>${escapeHtml(item.grade || '-')}</td>
      <td>${escapeHtml(item.textbook || '-')}</td>
      <td>
        <div class="row table-actions">
          <button class="ghost knowledge-edit" data-id="${escapeHtml(item.id)}">编辑</button>
          <button class="ghost danger knowledge-delete" data-id="${escapeHtml(item.id)}">删除</button>
        </div>
      </td>
    </tr>
  `).join('')
  body.querySelectorAll('.knowledge-edit').forEach((button) => {
    button.addEventListener('click', () => editKnowledge(button.dataset.id))
  })
  body.querySelectorAll('.knowledge-check').forEach((box) => {
    box.addEventListener('change', () => {
      if (box.checked) state.selectedKnowledgeIds.add(box.dataset.id)
      else state.selectedKnowledgeIds.delete(box.dataset.id)
      renderKnowledgeManager()
    })
  })
  body.querySelectorAll('.knowledge-delete').forEach((button) => {
    button.addEventListener('click', () => deleteKnowledge(button.dataset.id).catch((error) => toast(error.message)))
  })
}

function renderTaskList() {
  const list = $('taskList')
  if (!state.tasks.length) {
    list.innerHTML = '<div class="empty">暂无任务</div>'
    return
  }
  list.innerHTML = state.tasks.slice(0, 12).map((task) => {
    const active = task.id === state.selectedTaskId ? ' active' : ''
    const percent = Number(task.progress_percent || 0)
    return `
      <article class="task-item${active}" data-task-id="${escapeHtml(task.id)}">
        <div class="row">
          ${badge(task.status)}
          <small>${task.candidate_count || 0} 条</small>
        </div>
        <strong>${escapeHtml(task.knowledge_name || task.keyword)}</strong>
        <div class="progress"><i style="width:${percent}%"></i></div>
        <small>${percent}% · ${escapeHtml(task.message || task.stage || '')}</small>
      </article>
    `
  }).join('')
  list.querySelectorAll('.task-item').forEach((item) => {
    item.addEventListener('click', () => {
      state.selectedTaskId = item.dataset.taskId
      state.selectedCandidateIds.clear()
      refreshWorkbenchCandidates().catch((error) => toast(error.message))
      renderTaskList()
    })
  })
}

function renderTaskManager() {
  const body = $('taskManagerBody')
  if (!state.tasks.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty">暂无任务</td></tr>'
    return
  }
  body.innerHTML = state.tasks.map((task) => {
    const percent = Number(task.progress_percent || 0)
    return `
      <tr>
        <td>
          <div class="title">
            <strong>${escapeHtml(task.knowledge_name || task.keyword)}</strong>
            <small>${escapeHtml(task.keyword || '')}</small>
          </div>
        </td>
        <td>${badge(task.status)}</td>
        <td>
          <div class="download-state">
            <div class="progress"><i style="width:${percent}%"></i></div>
            <small>${percent}% · ${escapeHtml(task.message || task.stage || '')}</small>
          </div>
        </td>
        <td>${task.candidate_count || 0}</td>
        <td>${escapeHtml(task.created_at || '-')}</td>
        <td>
          <div class="row table-actions">
            <button class="ghost task-view-candidates" data-id="${escapeHtml(task.id)}">看候选</button>
            <button class="ghost danger task-delete" data-id="${escapeHtml(task.id)}">删除</button>
          </div>
        </td>
      </tr>
    `
  }).join('')
  body.querySelectorAll('.task-view-candidates').forEach((button) => {
    button.addEventListener('click', () => {
      state.candidateTaskFilter = button.dataset.id
      setView('candidates')
    })
  })
  body.querySelectorAll('.task-delete').forEach((button) => {
    button.addEventListener('click', () => deleteTask(button.dataset.id).catch((error) => toast(error.message)))
  })
}

function candidateRowCells(item, mode) {
  const checked = state.selectedCandidateIds.has(item.id) ? 'checked' : ''
  const progress = Number(item.download_progress_percent || 0)
  if (mode === 'library') {
    return `
      <td class="check"><input class="candidate-check" type="checkbox" data-id="${item.id}" ${checked} /></td>
      <td>
        <div class="title">
          <strong>${escapeHtml(item.knowledge_name || item.task_keyword || '-')}</strong>
          <small>${escapeHtml(item.task_keyword || '')}</small>
        </div>
      </td>
      <td>
        <div class="title">
          <a href="${escapeHtml(item.canonical_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title)}</a>
          <small>${escapeHtml(item.precheck_reason || '')}</small>
        </div>
      </td>
      <td>${escapeHtml(item.author || '-')}</td>
      <td>${formatNumber(item.view_count)}</td>
      <td><span class="score">${Number(item.precheck_score || 0).toFixed(1)}</span></td>
      <td>
        <div class="download-state">
          ${badge(item.download_status)}
          <div class="progress"><i style="width:${progress}%"></i></div>
          <small>${progress}% · ${escapeHtml(item.download_message || item.media_file || '')}</small>
        </div>
      </td>
      <td>
        <div class="row table-actions">
          <button class="ghost candidate-download-one" data-id="${item.id}">下载</button>
          <button class="ghost danger candidate-delete-one" data-id="${item.id}">删除</button>
        </div>
      </td>
    `
  }
  return `
    <td class="check"><input class="candidate-check" type="checkbox" data-id="${item.id}" ${checked} /></td>
    <td>${item.rank}</td>
    <td>
      <div class="title">
        <a href="${escapeHtml(item.canonical_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title)}</a>
        <small>${escapeHtml(item.precheck_reason || '')}</small>
      </div>
    </td>
    <td>${escapeHtml(item.author || '-')}</td>
    <td>${formatDuration(item.duration_seconds)}</td>
    <td>${formatNumber(item.view_count)}</td>
    <td><span class="score">${Number(item.precheck_score || 0).toFixed(1)}</span></td>
    <td>
      <div class="download-state">
        ${badge(item.download_status)}
        <div class="progress"><i style="width:${progress}%"></i></div>
        <small>${progress}% · ${escapeHtml(item.download_message || item.media_file || '')}</small>
      </div>
    </td>
  `
}

function bindCandidateTable(body) {
  body.querySelectorAll('.candidate-check').forEach((box) => {
    box.addEventListener('change', () => {
      const id = Number(box.dataset.id)
      if (box.checked) state.selectedCandidateIds.add(id)
      else state.selectedCandidateIds.delete(id)
    })
  })
  body.querySelectorAll('.candidate-download-one').forEach((button) => {
    button.addEventListener('click', () => downloadCandidate(Number(button.dataset.id)).catch((error) => toast(error.message)))
  })
  body.querySelectorAll('.candidate-delete-one').forEach((button) => {
    button.addEventListener('click', () => deleteCandidate(Number(button.dataset.id)).catch((error) => toast(error.message)))
  })
}

function renderWorkbenchCandidates() {
  const body = $('workbenchCandidateBody')
  if (!state.workbenchCandidates.length) {
    body.innerHTML = '<tr><td colspan="8" class="empty">暂无候选视频</td></tr>'
    return
  }
  body.innerHTML = state.workbenchCandidates.map((item) => `<tr>${candidateRowCells(item, 'workbench')}</tr>`).join('')
  bindCandidateTable(body)
}

function renderCandidateLibraryFilters() {
  const selected = state.tasks.some((task) => task.id === state.candidateTaskFilter) ? state.candidateTaskFilter : 'all'
  state.candidateTaskFilter = selected
  $('candidateTaskFilter').innerHTML = [
    `<option value="all" ${selected === 'all' ? 'selected' : ''}>全部任务</option>`,
    ...state.tasks.map((task) => `<option value="${escapeHtml(task.id)}" ${task.id === selected ? 'selected' : ''}>${escapeHtml(task.knowledge_name || task.keyword)}</option>`),
  ].join('')
  $('candidateSearch').value = state.candidateSearch
}

function renderCandidateLibrary() {
  renderCandidateLibraryFilters()
  const body = $('candidateBody')
  const rows = filteredCandidateRows()
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="8" class="empty">暂无候选视频</td></tr>'
    return
  }
  body.innerHTML = rows.map((item) => `<tr>${candidateRowCells(item, 'library')}</tr>`).join('')
  bindCandidateTable(body)
}

function renderImportPreview() {
  const container = $('importPreview')
  const button = $('importCommit')
  const preview = state.importPreview
  if (!preview) {
    container.classList.add('hidden')
    container.innerHTML = ''
    button.disabled = true
    return
  }
  const columns = preview.columns || []
  const fields = preview.standard_fields || []
  const rows = preview.sample_rows || []
  const options = ['<option value="">不映射</option>']
    .concat(columns.map((column) => `<option value="${escapeHtml(column)}">${escapeHtml(column)}</option>`))
    .join('')
  const sampleColumns = columns.slice(0, 6)
  container.innerHTML = `
    <div class="preview-meta">
      <span>文件：<b>${escapeHtml(preview.filename)}</b></span>
      <span>工作表：<b>${escapeHtml(preview.sheet_name)}</b></span>
      <span>表头：<b>第 ${preview.header_row} 行</b></span>
    </div>
    <div class="field-map">
      ${fields.map((field) => `
        <article>
          <div class="field-head">
            <strong>${escapeHtml(field.label)}</strong>
            ${field.required ? '<em>必填</em>' : ''}
          </div>
          <select class="field-select" data-field="${escapeHtml(field.key)}">${options}</select>
          <small>${escapeHtml(field.description || '')}</small>
        </article>
      `).join('')}
    </div>
    <div class="sample-wrap">
      <table>
        <thead><tr>${sampleColumns.map((column) => `<th>${escapeHtml(column)}</th>`).join('')}</tr></thead>
        <tbody>
          ${rows.slice(0, 5).map((row) => `
            <tr>${sampleColumns.map((column) => `<td>${escapeHtml(row[column] || '')}</td>`).join('')}</tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `
  container.querySelectorAll('.field-select').forEach((select) => {
    const field = select.dataset.field
    select.value = state.importMapping[field] || ''
    select.addEventListener('change', () => {
      state.importMapping[field] = select.value || null
    })
  })
  container.classList.remove('hidden')
  button.disabled = false
}

function renderAll() {
  renderSummary()
  renderAuth()
  renderTaskFilters()
  renderKnowledgeManager()
  renderTaskList()
  renderTaskManager()
  renderWorkbenchCandidates()
  renderCandidateLibrary()
  renderImportPreview()
}

async function refreshAll() {
  const [summary, auth, knowledge, tasks] = await Promise.all([
    api('/summary'),
    api('/auth'),
    api('/knowledge'),
    api('/tasks?limit=200'),
  ])
  state.summary = summary
  state.auth = auth
  state.knowledge = knowledge
  state.tasks = tasks
  if (state.selectedTaskId && !tasks.some((task) => task.id === state.selectedTaskId)) {
    state.selectedTaskId = ''
  }
  if (!state.selectedTaskId && tasks.length) state.selectedTaskId = tasks[0].id
  await refreshWorkbenchCandidates(false)
  if (state.view === 'candidates') await refreshCandidateLibrary(false)
  renderAll()
}

async function refreshWorkbenchCandidates(shouldRender = true) {
  if (!state.selectedTaskId) {
    state.workbenchCandidates = []
  } else {
    state.workbenchCandidates = await api(`/candidates?task_id=${encodeURIComponent(state.selectedTaskId)}&limit=200`)
  }
  if (shouldRender) renderWorkbenchCandidates()
}

async function refreshCandidateLibrary(shouldRender = true) {
  const suffix = state.candidateTaskFilter !== 'all'
    ? `?task_id=${encodeURIComponent(state.candidateTaskFilter)}&limit=500`
    : '?limit=500'
  state.candidates = await api(`/candidates${suffix}`)
  state.selectedCandidateIds = new Set(Array.from(state.selectedCandidateIds).filter((id) => (
    [...state.workbenchCandidates, ...state.candidates].some((item) => item.id === id)
  )))
  if (shouldRender) renderCandidateLibrary()
}

function knowledgePayloadFromForm() {
  return {
    subject: $('knowledgeSubject').value.trim(),
    stage: $('knowledgeStage').value.trim(),
    grade: $('knowledgeGrade').value.trim(),
    textbook: $('knowledgeTextbook').value.trim(),
    chapter: $('knowledgeChapter').value.trim(),
    group: $('knowledgeGroup').value.trim(),
    name: $('knowledgeName').value.trim(),
    aliases: $('knowledgeAliases').value.split(/[,，、;；\n]+/).map((item) => item.trim()).filter(Boolean),
    description: $('knowledgeDescription').value.trim(),
  }
}

function resetKnowledgeForm() {
  state.editingKnowledgeId = ''
  $('knowledgeFormTitle').textContent = '新建知识点'
  $('knowledgeSubject').value = '数学'
  $('knowledgeStage').value = '初中'
  $('knowledgeGrade').value = '九年级'
  $('knowledgeTextbook').value = '人教版'
  $('knowledgeChapter').value = ''
  $('knowledgeGroup').value = ''
  $('knowledgeName').value = ''
  $('knowledgeAliases').value = ''
  $('knowledgeDescription').value = ''
  $('knowledgeCancelEdit').classList.add('hidden')
}

function editKnowledge(pointId) {
  const item = state.knowledge.find((candidate) => candidate.id === pointId)
  if (!item) return
  state.editingKnowledgeId = item.id
  $('knowledgeFormTitle').textContent = '编辑知识点'
  $('knowledgeSubject').value = item.subject || ''
  $('knowledgeStage').value = item.stage || ''
  $('knowledgeGrade').value = item.grade || ''
  $('knowledgeTextbook').value = item.textbook || ''
  $('knowledgeChapter').value = item.chapter || ''
  $('knowledgeGroup').value = item.group || ''
  $('knowledgeName').value = item.name || ''
  $('knowledgeAliases').value = (item.aliases || []).join('，')
  $('knowledgeDescription').value = item.description || ''
}

async function saveKnowledge() {
  const payload = knowledgePayloadFromForm()
  if (!payload.subject || !payload.name) {
    toast('请填写学科和二级知识点')
    return
  }
  const path = state.editingKnowledgeId ? `/knowledge/${encodeURIComponent(state.editingKnowledgeId)}` : '/knowledge'
  await api(path, {
    method: state.editingKnowledgeId ? 'PUT' : 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  toast(state.editingKnowledgeId ? '知识点已更新' : '知识点已保存')
  resetKnowledgeForm()
  await refreshAll()
}

async function deleteKnowledge(pointId) {
  const item = state.knowledge.find((candidate) => candidate.id === pointId)
  if (!item) return
  if (!window.confirm(`删除知识点“${item.name}”？已生成的任务和候选不会自动删除。`)) return
  await api(`/knowledge/${encodeURIComponent(pointId)}/delete`, { method: 'POST' })
  if (state.editingKnowledgeId === pointId) resetKnowledgeForm()
  toast('知识点已删除')
  await refreshAll()
}

async function deleteSelectedKnowledge() {
  const ids = Array.from(state.selectedKnowledgeIds)
  if (!ids.length) {
    toast('请选择知识点')
    return
  }
  if (!window.confirm(`删除选中的 ${ids.length} 个知识点？已生成的任务和候选不会自动删除。`)) return
  const result = await api('/knowledge/delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  })
  state.selectedKnowledgeIds.clear()
  toast(`已删除 ${result.deleted} 个知识点`)
  await refreshAll()
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const value = String(reader.result || '')
      resolve(value.includes(',') ? value.split(',').pop() : value)
    }
    reader.onerror = () => reject(reader.error || new Error('读取文件失败'))
    reader.readAsDataURL(file)
  })
}

async function previewKnowledgeImport() {
  const file = $('importFile').files[0]
  if (!file) {
    toast('请选择知识点表')
    return
  }
  const contentBase64 = await fileToBase64(file)
  $('importCommit').disabled = true
  const preview = await api('/knowledge/import/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename: file.name, content_base64: contentBase64 }),
  })
  state.importPreview = preview
  state.importMapping = { ...(preview.suggested_mapping || {}) }
  renderImportPreview()
  toast('已读取表头，请确认字段映射')
}

async function commitKnowledgeImport() {
  const preview = state.importPreview
  if (!preview) {
    toast('请先预览知识点表')
    return
  }
  const missing = (preview.standard_fields || []).filter((field) => field.required && !state.importMapping[field.key])
  if (missing.length) {
    toast(`请补齐必填映射：${missing.map((field) => field.label).join('、')}`)
    return
  }
  const result = await api('/knowledge/import/commit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      upload_id: preview.upload_id,
      sheet_name: preview.sheet_name,
      header_row: preview.header_row,
      field_mapping: state.importMapping,
      mode: $('importMode').value || 'append',
      defaults: {
        subject: $('importSubject').value.trim() || '数学',
        stage: $('importStage').value.trim(),
        grade: $('importGrade').value.trim(),
        textbook: $('importTextbook').value.trim(),
      },
    }),
  })
  toast(`导入 ${result.imported_count} 个知识点`)
  state.importPreview = null
  state.importMapping = {}
  $('importFile').value = ''
  renderImportPreview()
  await refreshAll()
}

async function startTask() {
  const pointId = $('knowledgeSelect').value
  if (!pointId) {
    toast('请先在知识点管理中录入或导入知识点')
    return
  }
  const target = Number($('targetInput').value || 100)
  const result = await api('/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      knowledge_point_id: pointId,
      keyword: $('keywordInput').value.trim(),
      target_count: target,
    }),
  })
  state.selectedTaskId = result.task_id
  toast('粗筛任务已启动')
  await refreshAll()
}

async function deleteTask(taskId) {
  const task = state.tasks.find((item) => item.id === taskId)
  if (!task) return
  if (!window.confirm(`删除任务“${task.knowledge_name || task.keyword}”及其候选记录？`)) return
  await api(`/tasks/${encodeURIComponent(taskId)}/delete`, { method: 'POST' })
  if (state.selectedTaskId === taskId) {
    state.selectedTaskId = ''
    state.selectedCandidateIds.clear()
  }
  toast('任务已删除')
  await refreshAll()
}

async function clearFinishedTasks() {
  if (!window.confirm('清理所有已结束任务及其候选记录？运行中的任务会保留。')) return
  const result = await api('/tasks/clear?status=finished', { method: 'POST' })
  toast(`已清理任务 ${result.deleted_tasks} 个，候选 ${result.deleted_candidates} 条`)
  state.selectedTaskId = ''
  state.selectedCandidateIds.clear()
  await refreshAll()
}

async function downloadCandidate(id) {
  await api(`/candidates/${id}/download`, { method: 'POST' })
  toast('已加入下载队列')
  await refreshAll()
}

async function downloadSelected() {
  const ids = Array.from(state.selectedCandidateIds)
  if (!ids.length) {
    toast('请选择候选视频')
    return
  }
  await api('/candidates/download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ candidate_ids: ids }),
  })
  toast(`已加入下载队列：${ids.length} 条`)
  await refreshAll()
}

async function downloadAllCurrent() {
  const ids = state.workbenchCandidates
    .filter((item) => item.download_status !== 'COMPLETED' && item.download_status !== 'DOWNLOADING')
    .map((item) => item.id)
  if (!ids.length) {
    toast('当前任务没有待下载候选')
    return
  }
  await api('/candidates/download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ candidate_ids: ids }),
  })
  toast(`已加入下载队列：${ids.length} 条`)
  await refreshAll()
}

async function deleteCandidate(id) {
  if (!window.confirm('删除这条候选记录？')) return
  await api(`/candidates/${id}/delete`, { method: 'POST' })
  state.selectedCandidateIds.delete(id)
  toast('候选已删除')
  await refreshAll()
}

async function deleteSelectedCandidates() {
  const ids = Array.from(state.selectedCandidateIds)
  if (!ids.length) {
    toast('请选择候选视频')
    return
  }
  if (!window.confirm(`删除选中的 ${ids.length} 条候选记录？`)) return
  for (const id of ids) {
    await api(`/candidates/${id}/delete`, { method: 'POST' })
  }
  state.selectedCandidateIds.clear()
  toast(`已删除 ${ids.length} 条候选`)
  await refreshAll()
}

async function clearCandidateScope() {
  const isAll = state.candidateTaskFilter === 'all'
  if (!window.confirm(isAll ? '清空全部候选库记录？' : '清空当前任务的候选记录？')) return
  const suffix = isAll ? '' : `?task_id=${encodeURIComponent(state.candidateTaskFilter)}`
  const result = await api(`/candidates/clear${suffix}`, { method: 'POST' })
  state.selectedCandidateIds.clear()
  toast(`已删除 ${result.deleted} 条候选`)
  await refreshAll()
}

function bindEvents() {
  document.querySelectorAll('.nav-btn').forEach((button) => {
    button.addEventListener('click', () => setView(button.dataset.view))
  })
  $('authStart').addEventListener('click', async () => {
    await api('/auth/start', { method: 'POST' })
    toast('授权浏览器已打开')
  })
  $('authCheck').addEventListener('click', async () => {
    state.auth = await api('/auth?live=true')
    renderAuth()
    toast('授权状态已刷新')
  })
  $('authClear').addEventListener('click', async () => {
    await api('/auth/clear', { method: 'POST' })
    toast('授权状态已清除')
    await refreshAll()
  })
  $('taskSubjectFilter').addEventListener('change', (event) => {
    state.taskSubjectFilter = event.target.value
    renderTaskFilters()
  })
  $('taskGradeFilter').addEventListener('change', (event) => {
    state.taskGradeFilter = event.target.value
    renderTaskFilters()
  })
  $('taskKnowledgeSearch').addEventListener('input', (event) => {
    state.taskKnowledgeSearch = event.target.value
    renderTaskFilters()
  })
  $('knowledgeSubjectFilter').addEventListener('change', (event) => {
    state.knowledgeSubjectFilter = event.target.value
    renderKnowledgeManager()
  })
  $('knowledgeGradeFilter').addEventListener('change', (event) => {
    state.knowledgeGradeFilter = event.target.value
    renderKnowledgeManager()
  })
  $('knowledgeSearch').addEventListener('input', (event) => {
    state.knowledgeSearch = event.target.value
    renderKnowledgeManager()
  })
  $('candidateTaskFilter').addEventListener('change', (event) => {
    state.candidateTaskFilter = event.target.value
    state.selectedCandidateIds.clear()
    refreshCandidateLibrary().catch((error) => toast(error.message))
  })
  $('candidateSearch').addEventListener('input', (event) => {
    state.candidateSearch = event.target.value
    renderCandidateLibrary()
  })
  $('newKnowledge').addEventListener('click', resetKnowledgeForm)
  $('deleteSelectedKnowledge').addEventListener('click', () => deleteSelectedKnowledge().catch((error) => toast(error.message)))
  $('knowledgeSelectAll').addEventListener('change', (event) => {
    const rows = filteredKnowledgeManagerRows()
    state.selectedKnowledgeIds.clear()
    if (event.target.checked) {
      rows.forEach((item) => state.selectedKnowledgeIds.add(item.id))
    }
    renderKnowledgeManager()
  })
  $('knowledgeSave').addEventListener('click', () => saveKnowledge().catch((error) => toast(error.message)))
  $('knowledgeCancelEdit').addEventListener('click', resetKnowledgeForm)
  $('importPreviewBtn').addEventListener('click', () => previewKnowledgeImport().catch((error) => toast(error.message)))
  $('importCommit').addEventListener('click', () => commitKnowledgeImport().catch((error) => toast(error.message)))
  $('startTask').addEventListener('click', () => startTask().catch((error) => toast(error.message)))
  $('refreshBtn').addEventListener('click', () => refreshAll().catch((error) => toast(error.message)))
  $('refreshTasks').addEventListener('click', () => refreshAll().catch((error) => toast(error.message)))
  $('clearFinishedTasks').addEventListener('click', () => clearFinishedTasks().catch((error) => toast(error.message)))
  $('downloadSelected').addEventListener('click', () => downloadSelected().catch((error) => toast(error.message)))
  $('candidateDownloadSelected').addEventListener('click', () => downloadSelected().catch((error) => toast(error.message)))
  $('downloadAll').addEventListener('click', () => downloadAllCurrent().catch((error) => toast(error.message)))
  $('deleteSelectedCandidates').addEventListener('click', () => deleteSelectedCandidates().catch((error) => toast(error.message)))
  $('clearCandidates').addEventListener('click', () => clearCandidateScope().catch((error) => toast(error.message)))
  $('selectAll').addEventListener('change', (event) => {
    state.selectedCandidateIds.clear()
    if (event.target.checked) {
      state.workbenchCandidates.forEach((item) => state.selectedCandidateIds.add(item.id))
    }
    renderWorkbenchCandidates()
  })
  $('librarySelectAll').addEventListener('change', (event) => {
    state.selectedCandidateIds.clear()
    if (event.target.checked) {
      filteredCandidateRows().forEach((item) => state.selectedCandidateIds.add(item.id))
    }
    renderCandidateLibrary()
  })
}

bindEvents()
refreshAll().catch((error) => toast(error.message))
window.setInterval(() => refreshAll().catch(() => {}), 3000)
