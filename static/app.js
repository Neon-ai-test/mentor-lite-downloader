const state = {
  summary: {},
  auth: {},
  knowledge: [],
  tasks: [],
  candidates: [],
  selectedTaskId: '',
  selectedCandidateIds: new Set(),
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
  return `<span class="badge">${status || '-'}</span>`
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

function renderKnowledge() {
  $('knowledgeCount').textContent = state.knowledge.length
  const select = $('knowledgeSelect')
  select.innerHTML = state.knowledge.map((item) => {
    const label = [item.subject, item.grade, item.chapter, item.group, item.name].filter(Boolean).join(' / ')
    return `<option value="${item.id}">${escapeHtml(label)}</option>`
  }).join('')
}

function renderTasks() {
  const list = $('taskList')
  if (!state.tasks.length) {
    list.innerHTML = '<div class="empty">暂无任务</div>'
    return
  }
  list.innerHTML = state.tasks.map((task) => {
    const active = task.id === state.selectedTaskId ? ' active' : ''
    const percent = Number(task.progress_percent || 0)
    return `
      <article class="task-item${active}" data-task-id="${task.id}">
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
      refreshCandidates()
      renderTasks()
    })
  })
}

function renderCandidates() {
  const body = $('candidateBody')
  if (!state.candidates.length) {
    body.innerHTML = '<tr><td colspan="8" class="empty">暂无候选视频</td></tr>'
    return
  }
  body.innerHTML = state.candidates.map((item) => {
    const checked = state.selectedCandidateIds.has(item.id) ? 'checked' : ''
    const progress = Number(item.download_progress_percent || 0)
    return `
      <tr>
        <td class="check"><input class="candidate-check" type="checkbox" data-id="${item.id}" ${checked} /></td>
        <td>${item.rank}</td>
        <td>
          <div class="title">
            <a href="${item.canonical_url}" target="_blank" rel="noreferrer">${escapeHtml(item.title)}</a>
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
      </tr>
    `
  }).join('')
  body.querySelectorAll('.candidate-check').forEach((box) => {
    box.addEventListener('change', () => {
      const id = Number(box.dataset.id)
      if (box.checked) state.selectedCandidateIds.add(id)
      else state.selectedCandidateIds.delete(id)
    })
  })
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

async function refreshAll() {
  const [summary, auth, knowledge, tasks] = await Promise.all([
    api('/summary'),
    api('/auth'),
    api('/knowledge'),
    api('/tasks'),
  ])
  state.summary = summary
  state.auth = auth
  state.knowledge = knowledge
  state.tasks = tasks
  if (!state.selectedTaskId && tasks.length) state.selectedTaskId = tasks[0].id
  renderSummary()
  renderAuth()
  renderKnowledge()
  renderTasks()
  await refreshCandidates()
}

async function refreshCandidates() {
  if (!state.selectedTaskId) {
    state.candidates = []
  } else {
    state.candidates = await api(`/candidates?task_id=${encodeURIComponent(state.selectedTaskId)}&limit=200`)
  }
  renderCandidates()
}

async function saveManualKnowledge() {
  const payload = {
    subject: $('manualSubject').value.trim(),
    stage: $('manualStage').value.trim(),
    grade: $('manualGrade').value.trim(),
    textbook: $('manualTextbook').value.trim(),
    chapter: $('manualChapter').value.trim(),
    group: $('manualGroup').value.trim(),
    name: $('manualName').value.trim(),
    aliases: [],
    description: '',
  }
  if (!payload.subject || !payload.name) {
    toast('请填写学科和二级知识点')
    return
  }
  await api('/knowledge', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  $('manualName').value = ''
  toast('知识点已保存')
  await refreshAll()
}

async function importKnowledge() {
  const file = $('importFile').files[0]
  if (!file) {
    toast('请选择知识点表')
    return
  }
  const contentBase64 = await fileToBase64(file)
  const result = await api('/knowledge/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      filename: file.name,
      content_base64: contentBase64,
      subject: $('manualSubject').value.trim() || '数学',
      stage: $('manualStage').value.trim(),
      grade: $('manualGrade').value.trim(),
      textbook: $('manualTextbook').value.trim(),
    }),
  })
  toast(`导入 ${result.imported_count} 个知识点`)
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

async function startTask() {
  const pointId = $('knowledgeSelect').value
  if (!pointId) {
    toast('请先录入或导入知识点')
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
  await refreshCandidates()
}

async function downloadAllCurrent() {
  const ids = state.candidates
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
  await refreshCandidates()
}

function bindEvents() {
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
    await api('/auth', { method: 'DELETE' })
    toast('授权状态已清除')
    await refreshAll()
  })
  $('manualSave').addEventListener('click', () => saveManualKnowledge().catch((error) => toast(error.message)))
  $('importSubmit').addEventListener('click', () => importKnowledge().catch((error) => toast(error.message)))
  $('startTask').addEventListener('click', () => startTask().catch((error) => toast(error.message)))
  $('refreshBtn').addEventListener('click', () => refreshAll().catch((error) => toast(error.message)))
  $('downloadSelected').addEventListener('click', () => downloadSelected().catch((error) => toast(error.message)))
  $('downloadAll').addEventListener('click', () => downloadAllCurrent().catch((error) => toast(error.message)))
  $('selectAll').addEventListener('change', (event) => {
    state.selectedCandidateIds.clear()
    if (event.target.checked) {
      state.candidates.forEach((item) => state.selectedCandidateIds.add(item.id))
    }
    renderCandidates()
  })
}

bindEvents()
refreshAll().catch((error) => toast(error.message))
window.setInterval(() => refreshAll().catch(() => {}), 3000)
