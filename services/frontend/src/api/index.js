import axiosApi from './axios';

const data = (promise) => promise.then((r) => r.data.data);

export const authApi = {
  login: (username, password) => data(axiosApi.post('/auth/login', { username, password })),
  mfaSetup: (mfaToken) => data(axiosApi.post('/auth/mfa/setup', { mfaToken })),
  mfaVerify: (mfaToken, code) => data(axiosApi.post('/auth/mfa/verify', { mfaToken, code })),
  me: () => data(axiosApi.get('/auth/me')),
  updateProfile: (body) => data(axiosApi.patch('/auth/me', body)),
  changePassword: (currentPassword, newPassword) =>
    axiosApi.post('/auth/change-password', { currentPassword, newPassword }),
  completeOnboarding: () => data(axiosApi.post('/auth/onboarding/complete')),
  readiness: () => data(axiosApi.get('/auth/me/readiness')),
  setGithubToken: (token) => data(axiosApi.put('/auth/me/github-token', { token })),
  deleteGithubToken: () => data(axiosApi.delete('/auth/me/github-token')),
  activity: (before) => data(axiosApi.get('/auth/me/activity', { params: { before } })),
};

export const boardApi = {
  get: () => data(axiosApi.get('/board')),
  archived: () => data(axiosApi.get('/cards', { params: { archived: true } })),
};

export const cardsApi = {
  create: (body) => data(axiosApi.post('/cards', body)),
  get: (id) => data(axiosApi.get(`/cards/${id}`)),
  update: (id, body) => data(axiosApi.patch(`/cards/${id}`, body)),
  move: (id, listId, position) => data(axiosApi.post(`/cards/${id}/move`, { listId, position })),
  archive: (id) => data(axiosApi.post(`/cards/${id}/archive`)),
  unarchive: (id) => data(axiosApi.post(`/cards/${id}/unarchive`)),
  qa: (id, body) => data(axiosApi.post(`/cards/${id}/qa`, body)),
  // Rebase sobre la rama base de cada PR + merge de los PRs "Aprobado" (puede tardar: git + reintentos).
  // Rebase sobre la rama base de cada PR de los PRs abiertos, sin mergear.
  rebase: (id) => data(axiosApi.post(`/cards/${id}/rebase`, null, { timeout: 10 * 60 * 1000 })),
  merge: (id) => data(axiosApi.post(`/cards/${id}/merge`, null, { timeout: 10 * 60 * 1000 })),
  addComment: (id, body) => data(axiosApi.post(`/cards/${id}/comments`, { body })),
  deleteComment: (id, commentId) => axiosApi.delete(`/cards/${id}/comments/${commentId}`),
};

export const uploadsApi = {
  image: (file) => {
    const form = new FormData();
    form.append('file', file);
    return data(axiosApi.post('/uploads', form));
  },
};

export const githubApi = {
  pulls: (refresh = false) => data(axiosApi.get('/github/pulls', { params: refresh ? { refresh: true } : {} })),
  createCardFromPull: (repoKey, number) => data(axiosApi.post('/github/pulls/card', { repoKey, number })),
  // listIds=1&listIds=2 (lo que espera FastAPI), no listIds[]=1.
  testing: (listIds, refresh = false) => data(axiosApi.get('/github/testing', {
    params: { ...(listIds?.length ? { listIds } : {}), ...(refresh ? { refresh: true } : {}) },
    paramsSerializer: { indexes: null },
  })),
};

export const workflowApi = {
  tree: (refresh = false) => data(axiosApi.get('/workflow/tree', { params: refresh ? { refresh: true } : {} })),
  // ref: el sha del árbol que se está mostrando, para que archivo y árbol coincidan.
  file: (path, ref) => data(axiosApi.get('/workflow/file', { params: { path, ...(ref ? { ref } : {}) } })),
  chat: (messages) => data(axiosApi.post('/workflow/chat', { messages })),
  createPullRequest: (proposal) => data(axiosApi.post('/workflow/pull-requests', proposal)),
};

export const listsApi = {
  create: (name) => data(axiosApi.post('/lists', { name })),
  rename: (id, name) => data(axiosApi.put(`/lists/${id}`, { name })),
  setCollapsible: (id, collapsible) => data(axiosApi.put(`/lists/${id}`, { collapsible })),
  setColor: (id, color) => data(axiosApi.put(`/lists/${id}/color`, { color })),
  remove: (id) => axiosApi.delete(`/lists/${id}`),
  reorder: (ids) => axiosApi.post('/lists/reorder', { ids }),
};

export const reposApi = {
  list: () => data(axiosApi.get('/repositories')),
  create: (body) => data(axiosApi.post('/repositories', body)),
  update: (id, body) => data(axiosApi.put(`/repositories/${id}`, body)),
  setPreview: (id, body) => data(axiosApi.put(`/repositories/${id}/preview`, body)),
  remove: (id) => axiosApi.delete(`/repositories/${id}`),
};

export const previewEnvApi = {
  status: () => data(axiosApi.get('/preview-env')),
  connectVault: (addr, token) => data(axiosApi.post('/preview-env/vault', { addr, token })),
  setMysql: (body) => data(axiosApi.put('/preview-env/mysql', body)),
  prepareMine: () => data(axiosApi.post('/preview-env/me')),
};

export const repoHealthApi = {
  get: () => data(axiosApi.get('/repo-health')),
  refresh: () => data(axiosApi.post('/repo-health/refresh')),
  // Arregla lo que falla de un repo con un token de GitHub temporal (el backend no lo guarda).
  // Tarjeta en Backlog con lo que el repo no cumple (si ya hay una, no crea otra). Devuelve { data, message }.
  createCard: (key) => axiosApi.post(`/repo-health/${encodeURIComponent(key)}/card`).then((resp) => resp.data),
  fix: (key, token, checks) => data(axiosApi.post(`/repo-health/${encodeURIComponent(key)}/fix`, { token, checks: checks || null })),
};

export const previewDbsApi = {
  list: () => data(axiosApi.get('/preview-dbs')),
  tables: (username) => data(axiosApi.get(`/preview-dbs/${encodeURIComponent(username)}/tables`)),
  columns: (username, table) => data(axiosApi.get(`/preview-dbs/${encodeURIComponent(username)}/columns`, { params: { table } })),
  rows: (username, table, page, pageSize) =>
    data(axiosApi.get(`/preview-dbs/${encodeURIComponent(username)}/rows`, { params: { table, page, pageSize } })),
  prepare: (username) => data(axiosApi.post(`/preview-dbs/${encodeURIComponent(username)}/prepare`)),
  reset: (username, confirm) => data(axiosApi.post(`/preview-dbs/${encodeURIComponent(username)}/reset`, { confirm })),
};

export const previewsApi = {
  current: () => data(axiosApi.get('/previews')),
  start: (body) => data(axiosApi.post('/previews', body)),
  get: (id) => data(axiosApi.get(`/previews/${id}`)),
};

export const usersApi = {
  list: () => data(axiosApi.get('/users')),
  create: (body) => data(axiosApi.post('/users', body)),
  update: (id, body) => data(axiosApi.patch(`/users/${id}`, body)),
  remove: (id) => axiosApi.delete(`/users/${id}`),
  resetMfa: (id) => data(axiosApi.post(`/users/${id}/mfa/reset`)),
};

export const tokensApi = {
  list: () => data(axiosApi.get('/tokens')),
  create: (name) => data(axiosApi.post('/tokens', { name })),
  revoke: (id) => axiosApi.delete(`/tokens/${id}`),
};

export const notificationsApi = {
  list: () => data(axiosApi.get('/notifications')),
  read: (id) => axiosApi.post(`/notifications/${id}/read`),
  readAll: () => axiosApi.post('/notifications/read-all'),
  channels: () => data(axiosApi.get('/notifications/channels')),
  setChannels: (changes) => data(axiosApi.put('/notifications/channels', changes)),
};

export const vmsApi = {
  list: () => data(axiosApi.get('/vms')),
  // Solicitud de VM a los admins: la abierta del usuario (null si no hay) y el pedido nuevo.
  getRequest: () => data(axiosApi.get('/vms/request')),
  // Solo admins: las VMs de todos los usuarios y las solicitudes de VM abiertas.
  all: () => data(axiosApi.get('/vms/all')),
  requests: () => data(axiosApi.get('/vms/requests')),
  request: () => data(axiosApi.post('/vms/request')),
  create: (body) => data(axiosApi.post('/vms', body)),
  update: (id, body) => data(axiosApi.patch(`/vms/${id}`, body)),
  remove: (id) => axiosApi.delete(`/vms/${id}`),
  check: (id) => data(axiosApi.post(`/vms/${id}/check`)),
  // Prueba los datos del formulario sin guardarlos (vmId: partir de la VM guardada).
  test: (body, vmId) => data(axiosApi.post('/vms/test', body, { params: vmId ? { vmId } : {} })),
  // group: 'doctor' (problemas de la VM) o 'claude_rc' (pasos de la herramienta de Remote Control).
  diagnose: (id, group = 'doctor') => data(axiosApi.post(`/vms/${id}/diagnose`, null, { params: { group } })),
  fix: (id, issueId) => data(axiosApi.post(`/vms/${id}/fixes/${issueId}`)),
  task: (taskId) => data(axiosApi.get(`/vms/tasks/${taskId}`)),
  // Corre una skill de Claude (`claude -p`) en la VM sobre un PR registrado en la tarjeta.
  replySkill: (taskId, message) => data(axiosApi.post(`/vms/tasks/${taskId}/reply`, { message })),
  runSkill: (id, skill, cardId, prUrl) => data(axiosApi.post(`/vms/${id}/skills`, { skill, cardId, prUrl })),
};

export const toolsApi = {
  md5: (text) => data(axiosApi.post('/tools/md5', { text })),
};

export const systemApi = {
  updateStatus: () => data(axiosApi.get('/system/update')),
  requestUpdate: () => data(axiosApi.post('/system/update')),
};
