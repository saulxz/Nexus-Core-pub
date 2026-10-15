/* ============================================================
   Nexus-Core — Lógica del dashboard
   Controlador en JavaScript "vanilla": sin frameworks ni build.
   Se encarga de:
     · Consultar la telemetría y pintar las métricas/gráfica.
     · Listar, desplegar y eliminar contenedores Docker.
     · Ejecutar y mostrar los escaneos de red.
     · Mantener la conversación con el Agente de IA.
   Todas las peticiones usan rutas relativas (/api/...), de forma
   que el frontend funciona servido por el propio FastAPI.
   ============================================================ */

'use strict';

// ============================ Configuración ============================
const CONFIG = {
  API_BASE: '',                 // Mismo origen que sirve el frontend.
  POLL_INTERVAL_MS: 5000,       // Frecuencia de refresco de telemetría/contenedores.
  CHART_POINTS: 20,             // Puntos visibles en la gráfica.
  REQUEST_TIMEOUT_MS: 30000,    // Timeout para peticiones normales.
  CHAT_TIMEOUT_MS: 120000       // El agente con IA puede tardar más.
};

// ============================== Estado ==============================
const state = {
  chart: null,
  pollTimer: null,
  chatHistory: [],              // [{ role, content }]
  sendingChat: false,
  csrf: '',                     // Token CSRF de la sesión (cabecera X-CSRF-Token).
  username: '',                 // Usuario autenticado.
  services: [],                 // Último inventario recibido.
  servicesFilter: { query: '', state: 'all', scope: 'all' }
};

// ========================== Utilidades DOM ==========================
/** Devuelve un elemento por id. */
const $ = (id) => document.getElementById(id);

/** Escapa texto para insertarlo de forma segura en HTML. */
function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

/** Formatea segundos como "Xd Yh Zm". */
function formatUptime(totalSeconds) {
  const seconds = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const parts = [];
  if (days) parts.push(`${days}d`);
  if (hours) parts.push(`${hours}h`);
  parts.push(`${minutes}m`);
  return parts.join(' ');
}

/** Formatea un número en MiB a texto legible (MiB/GiB). */
function formatMemory(mib) {
  const value = Number(mib) || 0;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} GiB`;
  return `${value.toFixed(0)} MiB`;
}

/** Muestra una notificación tipo toast. */
function showToast(message, type = 'info') {
  const container = $('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  // Se elimina automáticamente a los 4 segundos.
  window.setTimeout(() => {
    toast.style.opacity = '0';
    window.setTimeout(() => toast.remove(), 300);
  }, 4000);
}

/** Realiza una petición fetch con timeout y manejo de errores uniforme. */
async function apiFetch(path, options = {}, timeoutMs = CONFIG.REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const method = (options.method || 'GET').toUpperCase();
    const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
    // Toda petición mutante exige el token CSRF de la sesión.
    if (method !== 'GET' && method !== 'HEAD' && state.csrf) {
      headers['X-CSRF-Token'] = state.csrf;
    }
    const response = await fetch(`${CONFIG.API_BASE}${path}`, {
      ...options,
      signal: controller.signal,
      headers
    });
    if (response.status === 401) {
      // Sesión caducada o inexistente: se vuelve a la pantalla de acceso.
      window.location.href = '/login.html';
      throw new Error('Sesión caducada.');
    }
    const text = await response.text();
    const data = text ? JSON.parse(text) : {};
    if (!response.ok) {
      const detail = data && data.detail ? data.detail : `HTTP ${response.status}`;
      throw new Error(detail);
    }
    return data;
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error('La petición agotó el tiempo de espera.');
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

// ========================= Estado de conexión =========================
function setOnlineStatus(isOnline) {
  const dot = $('status-dot');
  const text = $('status-text');
  if (!dot || !text) return;
  if (isOnline) {
    dot.className = 'h-2.5 w-2.5 rounded-full bg-brand shadow-glow';
    text.textContent = 'En línea';
  } else {
    dot.className = 'h-2.5 w-2.5 rounded-full bg-danger';
    text.textContent = 'Sin conexión';
  }
}

async function checkHealth() {
  try {
    await apiFetch('/api/health');
    setOnlineStatus(true);
  } catch (_error) {
    setOnlineStatus(false);
  }
}

// ======================= Sesión y autenticación =======================
/** Recupera la sesión activa (y su token CSRF). Si no hay, redirige al login. */
async function fetchSession() {
  const session = await apiFetch('/api/auth/session');
  state.csrf = session.csrf;
  state.username = session.username;
  return session;
}

/** Cierra la sesión y vuelve a la pantalla de acceso. */
async function logout() {
  try {
    await apiFetch('/api/auth/logout', { method: 'POST' });
  } catch (_error) {
    // Si la sesión ya había caducado, se ignora: igualmente se sale.
  }
  window.location.href = '/login.html';
}

// ============================ Métricas ============================
function updateBar(barId, value) {
  const bar = $(barId);
  if (!bar) return;
  const percent = Math.max(0, Math.min(100, Number(value) || 0));
  bar.style.width = `${percent}%`;
  // Cambia el color según umbrales de alerta.
  bar.classList.remove('warn', 'danger');
  if (percent >= 90) bar.classList.add('danger');
  else if (percent >= 75) bar.classList.add('warn');
}

function updateChart(cpu, ram) {
  if (!state.chart) return;
  const label = new Date().toLocaleTimeString('es-ES', { hour12: false });
  const labels = state.chart.data.labels;
  labels.push(label);
  state.chart.data.datasets[0].data.push(Number(cpu) || 0);
  state.chart.data.datasets[1].data.push(Number(ram) || 0);
  // Mantiene solo los últimos N puntos.
  while (labels.length > CONFIG.CHART_POINTS) {
    labels.shift();
    state.chart.data.datasets.forEach((dataset) => dataset.data.shift());
  }
  state.chart.update('none');
}

function renderMetrics(metrics) {
  $('metric-cpu').textContent = Number(metrics.cpu_percent).toFixed(1);
  $('metric-ram').textContent = Number(metrics.ram_percent).toFixed(1);
  $('metric-disk').textContent = Number(metrics.disk_percent).toFixed(1);
  updateBar('bar-cpu', metrics.cpu_percent);
  updateBar('bar-ram', metrics.ram_percent);
  updateBar('bar-disk', metrics.disk_percent);

  // Temperatura (puede no existir en un PC de desarrollo).
  const tempEl = $('metric-temp');
  const tempNote = $('metric-temp-note');
  if (metrics.cpu_temp_c === null || metrics.cpu_temp_c === undefined) {
    tempEl.textContent = '--';
    tempNote.textContent = 'Sensor no disponible';
  } else {
    tempEl.textContent = Number(metrics.cpu_temp_c).toFixed(1);
    tempNote.textContent = metrics.cpu_temp_c >= 75 ? '¡Temperatura alta!' : 'Rango normal';
    tempNote.className = metrics.cpu_temp_c >= 75
      ? 'mt-2 text-xs text-red-400'
      : 'mt-2 text-xs text-slate-500';
  }

  // Información adicional.
  $('info-cpu-count').textContent = `${metrics.cpu_count}`;
  $('info-ram-used').textContent = formatMemory(metrics.ram_used_mb);
  $('info-uptime').textContent = formatUptime(metrics.uptime_seconds);

  const updated = $('metrics-updated');
  if (updated) {
    updated.textContent = `Actualizado ${new Date().toLocaleTimeString('es-ES')}`;
  }

  updateChart(metrics.cpu_percent, metrics.ram_percent);
}

async function fetchMetrics() {
  try {
    const metrics = await apiFetch('/api/metrics');
    renderMetrics(metrics);
    setOnlineStatus(true);
  } catch (error) {
    console.error('Error obteniendo métricas:', error.message);
    setOnlineStatus(false);
  }
}

// ========================== Contenedores ==========================
function statusBadge(container) {
  const state = String(container.status || container.state || '').toLowerCase();
  let cls = 'badge-stopped';
  if (state === 'running') cls = 'badge-running';
  else if (state.includes('exit') || state.includes('dead')) cls = 'badge-error';
  return `<span class="badge ${cls}">${escapeHtml(state || 'desconocido')}</span>`;
}

function renderContainers(containers) {
  const tbody = $('containers-table');
  if (!tbody) return;

  if (!containers || containers.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-row">No hay contenedores en el host.</td></tr>';
    return;
  }

  tbody.innerHTML = containers
    .map((container) => {
      const ports = (container.ports || []).map(escapeHtml).join(', ') || '—';
      return `
        <tr>
          <td><code class="text-cyan-300">${escapeHtml(container.short_id || container.id)}</code></td>
          <td class="font-medium">${escapeHtml(container.name)}</td>
          <td>${escapeHtml(container.image)}</td>
          <td>${statusBadge(container)}</td>
          <td class="text-slate-400">${ports}</td>
          <td class="text-right">
            <button class="btn-danger" data-delete-id="${escapeHtml(container.id)}"
                    data-delete-name="${escapeHtml(container.name)}">Eliminar</button>
          </td>
        </tr>`;
    })
    .join('');

  // Asocia los botones de eliminar (delegación simple tras el render).
  tbody.querySelectorAll('[data-delete-id]').forEach((button) => {
    button.addEventListener('click', () =>
      deleteContainer(button.dataset.deleteId, button.dataset.deleteName)
    );
  });
}

async function fetchContainers() {
  try {
    const data = await apiFetch('/api/containers');
    renderContainers(data.containers || []);
  } catch (error) {
    console.error('Error listando contenedores:', error.message);
    const tbody = $('containers-table');
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="6" class="empty-row">Error: ${escapeHtml(error.message)}</td></tr>`;
    }
  }
}

async function deployContainer(event) {
  event.preventDefault();
  const image = $('deploy-image').value;
  const portValue = $('deploy-port').value.trim();
  const nameValue = $('deploy-name').value.trim();
  const submit = $('deploy-submit');

  const payload = { image };
  if (portValue) payload.port = Number(portValue);
  if (nameValue) payload.name = nameValue;

  submit.disabled = true;
  submit.textContent = 'Desplegando…';
  try {
    const result = await apiFetch('/api/containers', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    showToast(result.message, result.success ? 'success' : 'error');
    if (result.success) {
      $('deploy-port').value = '';
      $('deploy-name').value = '';
      await fetchContainers();
    }
  } catch (error) {
    showToast(`Error al desplegar: ${error.message}`, 'error');
  } finally {
    submit.disabled = false;
    submit.textContent = 'Desplegar contenedor';
  }
}

async function deleteContainer(containerId, containerName) {
  if (!window.confirm(`¿Eliminar el contenedor "${containerName}"?`)) return;
  try {
    const result = await apiFetch(`/api/containers/${encodeURIComponent(containerId)}`, {
      method: 'DELETE'
    });
    showToast(result.message, result.success ? 'success' : 'error');
    await fetchContainers();
  } catch (error) {
    showToast(`Error al eliminar: ${error.message}`, 'error');
  }
}

// ============================== Red ==============================
function renderScanResults(result) {
  const tbody = $('network-table');
  const summary = $('scan-summary');
  if (!tbody) return;

  if (result.error) {
    tbody.innerHTML = `<tr><td colspan="4" class="empty-row">${escapeHtml(result.error)}</td></tr>`;
    if (summary) summary.textContent = 'Escaneo rechazado';
    return;
  }

  if (!result.hosts || result.hosts.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty-row">No se encontraron hosts activos.</td></tr>';
  } else {
    tbody.innerHTML = result.hosts
      .map(
        (host) => `
        <tr>
          <td><code class="text-cyan-300">${escapeHtml(host.ip)}</code></td>
          <td><span class="badge badge-running">${escapeHtml(host.status)}</span></td>
          <td class="text-slate-400">${escapeHtml(host.mac || '—')}</td>
          <td>${escapeHtml(host.hostname || '—')}</td>
        </tr>`
      )
      .join('');
  }

  if (summary) {
    const time = new Date(result.scanned_at).toLocaleTimeString('es-ES');
    summary.textContent = `${result.count} hosts activos · ${time}`;
  }
}

async function scanNetwork(event) {
  event.preventDefault();
  const subnet = $('scan-subnet').value.trim();
  const submit = $('scan-submit');
  const tbody = $('network-table');

  submit.disabled = true;
  submit.innerHTML = '<span class="spinner"></span> Escaneando…';
  tbody.innerHTML = '<tr><td colspan="4" class="empty-row">Escaneando la red, puede tardar unos segundos…</td></tr>';

  try {
    const payload = subnet ? { subnet } : {};
    const result = await apiFetch('/api/network-scan', {
      method: 'POST',
      body: JSON.stringify(payload)
    }, 180000);
    renderScanResults(result);
    if (!result.error) {
      showToast(`Escaneo completado: ${result.count} hosts activos.`, 'success');
    } else {
      showToast(result.error, 'error');
    }
  } catch (error) {
    tbody.innerHTML = `<tr><td colspan="4" class="empty-row">Error: ${escapeHtml(error.message)}</td></tr>`;
    showToast(`Error en el escaneo: ${error.message}`, 'error');
  } finally {
    submit.disabled = false;
    submit.textContent = 'Escanear red';
  }
}

// ========================== Servicios ==========================
function serviceBadge(activeState) {
  const stateValue = String(activeState || '').toLowerCase();
  let cls = 'badge-stopped';
  if (stateValue === 'active') cls = 'badge-running';
  else if (stateValue === 'failed') cls = 'badge-error';
  return `<span class="badge ${cls}">${escapeHtml(stateValue || 'desconocido')}</span>`;
}

function renderServices(services) {
  state.services = services || [];
  applyServicesFilter();
}

function applyServicesFilter() {
  const tbody = $('services-table');
  if (!tbody) return;

  if (!state.services || state.services.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-row">No se encontraron servicios.</td></tr>';
    return;
  }

  const query = state.servicesFilter.query.trim().toLowerCase();
  const filtered = state.services.filter((service) => {
    const active = String(service.active_state || '').toLowerCase();
    if (state.servicesFilter.state === 'active' && active !== 'active') return false;
    if (state.servicesFilter.state === 'failed' && active !== 'failed') return false;
    if (state.servicesFilter.state === 'inactive' && active === 'active') return false;
    if (state.servicesFilter.scope === 'target' && !service.is_target) return false;
    if (query) {
      const haystack = `${service.name} ${service.description || ''} ${service.friendly_name || ''}`.toLowerCase();
      if (!haystack.includes(query)) return false;
    }
    return true;
  });

  const counter = $('services-count');
  if (counter) {
    counter.textContent = filtered.length === state.services.length
      ? `${state.services.length} servicios`
      : `${filtered.length} de ${state.services.length} servicios`;
  }

  if (filtered.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-row">Ningún servicio coincide con el filtro.</td></tr>';
    return;
  }

  tbody.innerHTML = filtered
    .map((service) => {
      const label = service.friendly_name
        ? `${service.friendly_name} · ${service.description}`
        : (service.description || '—');
      return `
        <tr>
          <td><code class="text-cyan-300">${escapeHtml(service.name)}</code></td>
          <td>${serviceBadge(service.active_state)}</td>
          <td class="text-slate-400">${escapeHtml(service.sub_state || '—')}</td>
          <td class="text-slate-400">${escapeHtml(label)}</td>
          <td class="text-right">${serviceActionsHtml(service.name, service.active_state)}</td>
        </tr>`;
    })
    .join('');

  tbody.querySelectorAll('[data-service-name]').forEach((button) => {
    button.addEventListener('click', () => showServiceDetail(button.dataset.serviceName));
  });
  tbody.querySelectorAll('[data-svc-action]').forEach((button) => {
    button.addEventListener('click', () =>
      serviceAction(button.dataset.svcUnit, button.dataset.svcAction)
    );
  });
}

async function fetchServices() {
  const tbody = $('services-table');
  if (!tbody) return;
  try {
    const data = await apiFetch('/api/services');
    if (!data.available) {
      state.services = [];
      const counter = $('services-count');
      if (counter) counter.textContent = '';
      tbody.innerHTML = `<tr><td colspan="5" class="empty-row">${escapeHtml(data.reason || 'systemd no disponible en este sistema.')}</td></tr>`;
      return;
    }
    renderServices(data.services || []);
  } catch (error) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty-row">Error: ${escapeHtml(error.message)}</td></tr>`;
  }
}

async function showServiceDetail(name) {
  const panel = $('service-detail');
  if (!panel) return;
  panel.classList.remove('hidden');
  $('service-detail-title').textContent = name;
  $('service-detail-body').innerHTML = '<p class="text-xs text-slate-400">Cargando…</p>';
  $('service-detail-logs').textContent = '';

  try {
    const encoded = encodeURIComponent(name);
    const [detail, logs] = await Promise.all([
      apiFetch(`/api/services/${encoded}`),
      apiFetch(`/api/services/${encoded}/logs?lines=100`)
    ]);
    const memory = detail.memory_bytes
      ? formatMemory(detail.memory_bytes / (1024 * 1024))
      : '—';
    const configService = configServiceFor(name);
    $('service-detail-body').innerHTML = `
      <div class="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <div><p class="metric-label">Estado</p><p class="mt-1">${serviceBadge(detail.active_state)} <span class="text-xs text-slate-400">${escapeHtml(detail.sub_state)}</span></p></div>
        <div><p class="metric-label">PID principal</p><p class="mt-1">${detail.main_pid || '—'}</p></div>
        <div><p class="metric-label">Memoria</p><p class="mt-1">${memory}</p></div>
        <div><p class="metric-label">Última activación</p><p class="mt-1 text-xs">${escapeHtml(detail.active_enter_timestamp || '—')}</p></div>
      </div>
      <p class="mt-3 text-xs text-slate-400">${escapeHtml(detail.description || '')}</p>
      <p class="mt-1 text-xs text-slate-500">Fichero de unidad: <code>${escapeHtml(detail.fragment_path || '—')}</code></p>
      <div class="mt-3 flex flex-wrap gap-2">
        ${serviceActionsHtml(name, detail.active_state)}
        ${configService ? `<button class="btn-ghost text-xs" data-config-service="${configService}">Configurar</button>` : ''}
      </div>`;
    $('service-detail-body').querySelectorAll('[data-detail-action], [data-svc-action]').forEach((button) => {
      const action = button.dataset.detailAction || button.dataset.svcAction;
      button.addEventListener('click', () => serviceAction(name, action));
    });
    const configButton = $('service-detail-body').querySelector('[data-config-service]');
    if (configButton) {
      configButton.addEventListener('click', () => openServiceConfig(configButton.dataset.configService));
    }
    $('service-detail-logs').textContent = (logs.available && logs.count > 0)
      ? logs.lines.join('\n')
      : (logs.reason || 'No hay logs disponibles.');
  } catch (error) {
    $('service-detail-body').innerHTML = `<p class="text-xs text-red-400">Error: ${escapeHtml(error.message)}</p>`;
  }
}

// =================== Acciones sobre servicios (Fase 2) ===================
const SERVICE_ACTION_LABELS = {
  start: 'Arrancar',
  stop: 'Parar',
  restart: 'Reiniciar',
  reload: 'Recargar'
};

function serviceActionsHtml(unit, activeState) {
  const stateValue = String(activeState || '').toLowerCase();
  const name = escapeHtml(unit);
  const parts = [];
  if (stateValue === 'active') {
    parts.push(`<button class="btn-ghost text-xs" data-svc-action="restart" data-svc-unit="${name}">Reiniciar</button>`);
    parts.push(`<button class="btn-ghost text-xs" data-svc-action="stop" data-svc-unit="${name}">Parar</button>`);
  } else {
    parts.push(`<button class="btn-ghost text-xs" data-svc-action="start" data-svc-unit="${name}">Arrancar</button>`);
  }
  parts.push(`<button class="btn-ghost text-xs" data-service-name="${name}">Detalle</button>`);
  return parts.join(' ');
}

async function serviceAction(unit, action) {
  const label = SERVICE_ACTION_LABELS[action] || action;
  if (!window.confirm(`¿${label} el servicio ${unit}?`)) return;
  try {
    const result = await apiFetch(`/api/services/${encodeURIComponent(unit)}/action`, {
      method: 'POST',
      body: JSON.stringify({ action })
    });
    showToast(result.message, result.success ? 'success' : 'error');
    await fetchServices();
    if (!$('service-detail').classList.contains('hidden')) {
      await showServiceDetail(unit);
    }
  } catch (error) {
    showToast(`Error al ejecutar la acción: ${error.message}`, 'error');
  }
}

// ============== Configuración guiada de servicios (Fases 3/4) ==============
const configState = { service: null, bound: false };

function configServiceFor(unit) {
  if (['smbd.service', 'nmbd.service', 'smb.service'].includes(unit)) return 'samba';
  if (unit === 'dnsmasq.service') return 'dnsmasq';
  return null;
}

function setConfigStatus(message, kind) {
  const box = $('config-status');
  const palette = {
    ok: 'border-emerald-400/40 bg-emerald-400/10 text-emerald-400',
    error: 'border-red-400/40 bg-red-400/10 text-red-400',
    info: 'border-slate-700/70 bg-nexus-900/70 text-slate-300'
  };
  box.textContent = message;
  box.className = 'mt-3 rounded-sm border px-3 py-2 text-xs ' + (palette[kind] || palette.info);
  box.classList.remove('hidden');
}

async function openServiceConfig(service) {
  const panel = $('config-panel');
  if (!panel) return;
  configState.service = service;
  panel.classList.remove('hidden');
  $('config-title').textContent = service === 'samba' ? 'Samba (smb.conf)' : 'dnsmasq (DNS + DHCP)';
  $('config-status').classList.add('hidden');
  $('config-preview').textContent = '';
  $('config-note').classList.add('hidden');
  $('config-form').innerHTML = '<p class="text-xs text-slate-400">Cargando configuración…</p>';
  try {
    const data = await apiFetch(`/api/config/${service}`);
    if (!data.managed && data.exists) {
      $('config-note').textContent =
        'El fichero actual no está gestionado por el panel: al guardar se generará desde este formulario (siempre con copia de seguridad previa). ' +
        (data.reason || '');
      $('config-note').classList.remove('hidden');
    } else if (!data.exists) {
      $('config-note').textContent = `El fichero ${data.path} todavía no existe; al guardar se creará con esta configuración.`;
      $('config-note').classList.remove('hidden');
    }
    $('config-form').innerHTML =
      service === 'samba' ? sambaFormHtml(data.config) : dnsmasqFormHtml(data.config);
    bindConfigFormEvents();
    await loadBackups(service);
  } catch (error) {
    $('config-form').innerHTML = `<p class="text-xs text-red-400">Error: ${escapeHtml(error.message)}</p>`;
  }
}

function sambaFormHtml(model) {
  const shares = model.shares || [];
  return `
    <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <label class="flex flex-col gap-1"><span class="metric-label">Grupo de trabajo</span>
        <input id="cfg-workgroup" class="input-field" value="${escapeHtml(model.workgroup || '')}" /></label>
      <label class="flex flex-col gap-1 sm:col-span-2"><span class="metric-label">Descripción del servidor</span>
        <input id="cfg-server-string" class="input-field" value="${escapeHtml(model.server_string || '')}" /></label>
      <label class="flex flex-col gap-1"><span class="metric-label">Acceso invitado</span>
        <select id="cfg-map-to-guest" class="input-field">
          <option value="Never" ${model.map_to_guest === 'Never' ? 'selected' : ''}>Never (recomendado)</option>
          <option value="Bad User" ${model.map_to_guest === 'Bad User' ? 'selected' : ''}>Bad User</option>
          <option value="Bad Password" ${model.map_to_guest === 'Bad Password' ? 'selected' : ''}>Bad Password</option>
        </select></label>
    </div>
    <div class="mt-4 flex items-center justify-between">
      <span class="metric-label">Shares publicadas</span>
      <button type="button" class="btn-ghost text-xs" id="cfg-add-share">+ Añadir share</button>
    </div>
    <div id="cfg-shares" class="mt-2 space-y-3">
      ${shares.map((share, index) => sambaShareRow(index, share)).join('')}
    </div>`;
}

function sambaShareRow(index, share) {
  return `
    <fieldset class="rounded-sm border border-slate-800 p-3" data-share-index="${index}">
      <div class="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <label class="flex flex-col gap-1"><span class="metric-label">Nombre</span>
          <input class="input-field" data-field="name" value="${escapeHtml(share.name || '')}" /></label>
        <label class="flex flex-col gap-1 sm:col-span-2"><span class="metric-label">Ruta</span>
          <input class="input-field" data-field="path" value="${escapeHtml(share.path || '')}" /></label>
        <label class="flex flex-col gap-1 sm:col-span-3"><span class="metric-label">Comentario</span>
          <input class="input-field" data-field="comment" value="${escapeHtml(share.comment || '')}" /></label>
      </div>
      <div class="mt-2 flex flex-wrap items-center gap-3 text-xs">
        <label class="flex items-center gap-2"><input type="checkbox" data-field="read_only" ${share.read_only ? 'checked' : ''} /><span>Solo lectura</span></label>
        <label class="flex items-center gap-2"><input type="checkbox" data-field="browseable" ${share.browseable ? 'checked' : ''} /><span>Visible</span></label>
        <label class="flex items-center gap-2"><input type="checkbox" data-field="guest_ok" ${share.guest_ok ? 'checked' : ''} /><span>Invitados</span></label>
        <label class="flex flex-1 items-center gap-2"><span class="whitespace-nowrap">Usuarios</span>
          <input class="input-field" data-field="valid_users" value="${escapeHtml(share.valid_users || '')}" placeholder="(todos)" /></label>
        <button type="button" class="btn-ghost text-xs" data-remove-share="${index}">Quitar</button>
      </div>
    </fieldset>`;
}

function dnsmasqFormHtml(model) {
  const dhcp = model.dhcp || {};
  return `
    <label class="flex flex-col gap-1"><span class="metric-label">Servidores DNS upstream (uno por línea)</span>
      <textarea id="cfg-dns-servers" rows="3" class="input-field" placeholder="9.9.9.9">${escapeHtml((model.servers || []).join('\n'))}</textarea></label>

    <div class="mt-4 flex items-center justify-between">
      <span class="metric-label">Registros locales (nombre → IP)</span>
      <button type="button" class="btn-ghost text-xs" id="cfg-add-record">+ Añadir registro</button>
    </div>
    <div id="cfg-records" class="mt-2 space-y-2">
      ${(model.records || []).map((record, index) => dnsRecordRow(index, record)).join('')}
    </div>

    <fieldset class="mt-4 rounded-sm border border-slate-800 p-3">
      <label class="flex items-center gap-2 text-sm">
        <input type="checkbox" id="cfg-dhcp-enabled" ${dhcp.enabled ? 'checked' : ''} />
        <span class="font-semibold">Activar servidor DHCP (dnsmasq)</span>
      </label>
      <div class="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
        <label class="flex flex-col gap-1"><span class="metric-label">Inicio</span>
          <input id="cfg-dhcp-start" class="input-field" value="${escapeHtml(dhcp.start || '')}" /></label>
        <label class="flex flex-col gap-1"><span class="metric-label">Fin</span>
          <input id="cfg-dhcp-end" class="input-field" value="${escapeHtml(dhcp.end || '')}" /></label>
        <label class="flex flex-col gap-1"><span class="metric-label">Concesión</span>
          <input id="cfg-dhcp-lease" class="input-field" value="${escapeHtml(dhcp.lease_time || '12h')}" /></label>
      </div>
      <div class="mt-3 flex items-center justify-between">
        <span class="metric-label">Reservas por MAC</span>
        <button type="button" class="btn-ghost text-xs" id="cfg-add-reservation">+ Añadir reserva</button>
      </div>
      <div id="cfg-reservations" class="mt-2 space-y-2">
        ${(model.reservations || []).map((reservation, index) => reservationRow(index, reservation)).join('')}
      </div>
      <button type="button" class="btn-ghost mt-3 text-xs" id="cfg-dhcp-check">Comprobar si hay otro DHCP en la red</button>
    </fieldset>`;
}

function dnsRecordRow(index, record) {
  return `
    <div class="flex flex-wrap items-center gap-2" data-record-index="${index}">
      <input class="input-field" data-field="name" placeholder="nas.local" value="${escapeHtml(record.name || '')}" />
      <input class="input-field" data-field="address" placeholder="192.168.1.10" value="${escapeHtml(record.address || '')}" />
      <button type="button" class="btn-ghost text-xs" data-remove-record="${index}">Quitar</button>
    </div>`;
}

function reservationRow(index, reservation) {
  return `
    <div class="flex flex-wrap items-center gap-2" data-reservation-index="${index}">
      <input class="input-field" data-field="mac" placeholder="aa:bb:cc:dd:ee:ff" value="${escapeHtml(reservation.mac || '')}" />
      <input class="input-field" data-field="address" placeholder="192.168.1.20" value="${escapeHtml(reservation.address || '')}" />
      <input class="input-field" data-field="hostname" placeholder="nombre (opcional)" value="${escapeHtml(reservation.hostname || '')}" />
      <button type="button" class="btn-ghost text-xs" data-remove-reservation="${index}">Quitar</button>
    </div>`;
}

function bindConfigFormEvents() {
  if (configState.bound) return;
  configState.bound = true;
  $('config-form').addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (target.id === 'cfg-add-share') {
      $('cfg-shares').insertAdjacentHTML(
        'beforeend',
        sambaShareRow(Date.now(), { read_only: true, browseable: true })
      );
    } else if (target.id === 'cfg-add-record') {
      $('cfg-records').insertAdjacentHTML('beforeend', dnsRecordRow(Date.now(), {}));
    } else if (target.id === 'cfg-add-reservation') {
      $('cfg-reservations').insertAdjacentHTML('beforeend', reservationRow(Date.now(), {}));
    } else if (target.dataset.removeShare !== undefined) {
      target.closest('fieldset')?.remove();
    } else if (target.dataset.removeRecord !== undefined) {
      target.closest('[data-record-index]')?.remove();
    } else if (target.dataset.removeReservation !== undefined) {
      target.closest('[data-reservation-index]')?.remove();
    } else if (target.id === 'cfg-dhcp-check') {
      checkDhcpConflicts();
    }
  });
}

function collectSambaConfig() {
  const shares = [];
  document.querySelectorAll('[data-share-index]').forEach((fieldset) => {
    const field = (fieldName) => fieldset.querySelector(`[data-field="${fieldName}"]`);
    shares.push({
      name: field('name').value.trim(),
      path: field('path').value.trim(),
      comment: field('comment').value.trim(),
      read_only: field('read_only').checked,
      browseable: field('browseable').checked,
      guest_ok: field('guest_ok').checked,
      valid_users: field('valid_users').value.trim()
    });
  });
  return {
    workgroup: $('cfg-workgroup').value.trim(),
    server_string: $('cfg-server-string').value.trim(),
    map_to_guest: $('cfg-map-to-guest').value,
    shares
  };
}

function collectDnsmasqConfig() {
  const records = [];
  document.querySelectorAll('[data-record-index]').forEach((row) => {
    const field = (fieldName) => row.querySelector(`[data-field="${fieldName}"]`);
    records.push({
      name: field('name').value.trim(),
      address: field('address').value.trim()
    });
  });
  const reservations = [];
  document.querySelectorAll('[data-reservation-index]').forEach((row) => {
    const field = (fieldName) => row.querySelector(`[data-field="${fieldName}"]`);
    reservations.push({
      mac: field('mac').value.trim(),
      address: field('address').value.trim(),
      hostname: field('hostname').value.trim()
    });
  });
  return {
    servers: $('cfg-dns-servers').value.split('\n').map((line) => line.trim()).filter(Boolean),
    records,
    dhcp: {
      enabled: $('cfg-dhcp-enabled').checked,
      start: $('cfg-dhcp-start').value.trim(),
      end: $('cfg-dhcp-end').value.trim(),
      lease_time: $('cfg-dhcp-lease').value.trim() || '12h'
    },
    reservations
  };
}

function collectConfig() {
  return configState.service === 'samba' ? collectSambaConfig() : collectDnsmasqConfig();
}

async function validateConfig() {
  if (!configState.service) return;
  try {
    const result = await apiFetch(`/api/config/${configState.service}/validate`, {
      method: 'POST',
      body: JSON.stringify({ config: collectConfig() })
    });
    $('config-preview').textContent = result.preview || '';
    const extra = (result.warnings || []).length ? ' · Avisos: ' + result.warnings.join(' ') : '';
    setConfigStatus(result.message + extra, result.ok ? 'ok' : 'error');
  } catch (error) {
    setConfigStatus('Error al validar: ' + error.message, 'error');
  }
}

async function saveConfig(force = false) {
  if (!configState.service) return;
  try {
    const result = await apiFetch(`/api/config/${configState.service}`, {
      method: 'PUT',
      body: JSON.stringify({ config: collectConfig(), force })
    });
    $('config-preview').textContent = result.preview || '';
    const extra = (result.warnings || []).length ? ' · Avisos: ' + result.warnings.join(' ') : '';
    setConfigStatus(result.message + extra, result.success ? 'ok' : 'info');
    if (result.success) await loadBackups(configState.service);
  } catch (error) {
    if (String(error.message).includes('DHCP') && !force) {
      if (window.confirm(error.message + '\n\n¿Forzar la activación de todas formas?')) {
        return saveConfig(true);
      }
      setConfigStatus(error.message, 'error');
      return;
    }
    setConfigStatus('Error al guardar: ' + error.message, 'error');
  }
}

async function checkDhcpConflicts() {
  try {
    const result = await apiFetch('/api/config/dnsmasq/dhcp-check', { method: 'POST' });
    if (!result.available) {
      setConfigStatus('No se pudo sondear la red: ' + (result.reason || 'sonda no disponible.'), 'info');
      return;
    }
    if (result.foreign.length) {
      setConfigStatus(
        'Se detectaron OTROS servidores DHCP: ' + result.foreign.join(', ') +
        '. Activar el DHCP aquí puede dar problemas.',
        'error'
      );
    } else {
      setConfigStatus(
        'Sonda correcta: no se detectaron servidores DHCP ajenos' +
        (result.ours.length ? ' (el nuestro: ' + result.ours.join(', ') + ')' : '') + '.',
        'ok'
      );
    }
  } catch (error) {
    setConfigStatus('Error en la sonda DHCP: ' + error.message, 'error');
  }
}

async function loadBackups(service) {
  const box = $('config-backups');
  if (!box) return;
  try {
    const data = await apiFetch(`/api/config/${service}/backups`);
    if (!data.backups || data.backups.length === 0) {
      box.innerHTML = '<p class="text-slate-500">No hay copias de seguridad todavía.</p>';
      return;
    }
    box.innerHTML = '<p class="metric-label mb-1">Copias de seguridad (últimas 20)</p>' +
      data.backups.map((backup) => `
      <div class="flex flex-wrap items-center justify-between gap-2 border-t border-slate-800 py-1">
        <span class="text-xs"><code>${escapeHtml(backup.name)}</code> · ${backup.size} B · ${escapeHtml(backup.modified)}</span>
        <button class="btn-ghost text-xs" data-restore-backup="${escapeHtml(backup.name)}">Restaurar</button>
      </div>`).join('');
    box.querySelectorAll('[data-restore-backup]').forEach((button) => {
      button.addEventListener('click', () => restoreBackup(service, button.dataset.restoreBackup));
    });
  } catch (error) {
    box.innerHTML = `<p class="text-xs text-red-400">Error cargando copias: ${escapeHtml(error.message)}</p>`;
  }
}

async function restoreBackup(service, name) {
  if (!window.confirm(`¿Restaurar la copia ${name}? La configuración actual se sustituirá y el servicio se recargará.`)) return;
  try {
    const result = await apiFetch(`/api/config/${service}/restore`, {
      method: 'POST',
      body: JSON.stringify({ backup: name })
    });
    setConfigStatus(result.message, result.success ? 'ok' : 'error');
    showToast(result.message, result.success ? 'success' : 'error');
  } catch (error) {
    setConfigStatus('Error al restaurar: ' + error.message, 'error');
  }
}

// ============================ Chat IA ============================
function appendChatMessage(role, content, actions) {
  const container = $('chat-messages');
  if (!container) return;

  const bubble = document.createElement('div');
  bubble.className = `chat-bubble ${role}`;
  bubble.textContent = content;

  // Si el agente ejecutó herramientas, se muestran como chips informativos.
  if (actions && actions.length > 0) {
    actions.forEach((action) => {
      const chip = document.createElement('span');
      chip.className = 'action-chip';
      chip.textContent = `⚙ ${action.tool}`;
      bubble.appendChild(document.createElement('br'));
      bubble.appendChild(chip);
    });
  }

  container.appendChild(bubble);
  container.scrollTop = container.scrollHeight;
}

let typingTimer = null;

function showTypingIndicator() {
  const container = $('chat-messages');
  if (!container) return null;
  const indicator = document.createElement('div');
  indicator.className = 'chat-bubble assistant';
  indicator.id = 'typing-indicator';
  indicator.innerHTML =
    '<span class="text-xs text-slate-400" id="typing-text">Esperando respuesta…</span> ' +
    '<span class="typing"><span></span><span></span><span></span></span>';
  container.appendChild(indicator);
  container.scrollTop = container.scrollHeight;
  // Si el modelo tarda mucho, se avisa sin cortar la espera.
  if (typingTimer) window.clearTimeout(typingTimer);
  typingTimer = window.setTimeout(() => {
    const label = $('typing-text');
    if (label) label.textContent = 'El modelo está tardando más de lo normal…';
  }, 15000);
  return indicator;
}

function removeTypingIndicator() {
  if (typingTimer) {
    window.clearTimeout(typingTimer);
    typingTimer = null;
  }
  const indicator = $('typing-indicator');
  if (indicator) indicator.remove();
}

async function sendChat(event) {
  event.preventDefault();
  if (state.sendingChat) return;

  const input = $('chat-input');
  const message = input.value.trim();
  if (!message) return;

  // Muestra el mensaje del usuario y lo añade al historial.
  appendChatMessage('user', message);
  state.chatHistory.push({ role: 'user', content: message });
  input.value = '';

  state.sendingChat = true;
  const sendButton = $('chat-send');
  sendButton.disabled = true;
  showTypingIndicator();

  try {
    const result = await apiFetch('/api/chat', {
      method: 'POST',
      body: JSON.stringify({
        message,
        history: state.chatHistory.slice(-10, -1) // Historial previo (sin el mensaje actual).
      })
    }, CONFIG.CHAT_TIMEOUT_MS);

    removeTypingIndicator();
    appendChatMessage('assistant', result.reply, result.actions);
    state.chatHistory.push({ role: 'assistant', content: result.reply });
    renderPendingActions(result.pending_actions);

    // Notifica si la IA ejecutó alguna acción sobre el sistema.
    if (result.actions && result.actions.length > 0) {
      showToast(`El agente ejecutó: ${result.actions.map((a) => a.tool).join(', ')}`, 'success');
      // Refresca las vistas afectadas para reflejar los cambios.
      fetchMetrics();
      fetchContainers();
    }
  } catch (error) {
    removeTypingIndicator();
    appendChatMessage('assistant', `No se pudo obtener respuesta del asistente: ${error.message}`);
    showToast(`Error del asistente: ${error.message}`, 'error');
  } finally {
    state.sendingChat = false;
    sendButton.disabled = false;
    input.focus();
  }
}

function clearChat() {
  state.chatHistory = [];
  const container = $('chat-messages');
  if (container) {
    container.innerHTML =
      '<div class="chat-bubble assistant">Conversación reiniciada. ¿En qué te ayudo?</div>';
  }
}

// ==================== Red: cambio de IP (Fase 6) ====================
let netcfgToken = null;
let netcfgTimer = null;

function setNetStatus(message, kind) {
  const box = $('netcfg-status');
  const palette = {
    ok: 'border-emerald-400/40 bg-emerald-400/10 text-emerald-400',
    error: 'border-red-400/40 bg-red-400/10 text-red-400',
    info: 'border-slate-700/70 bg-nexus-900/70 text-slate-300'
  };
  box.textContent = message;
  box.className = 'mt-3 rounded-sm border px-3 py-2 text-xs ' + (palette[kind] || palette.info);
  box.classList.remove('hidden');
}

async function fetchNetworkState() {
  try {
    const state = await apiFetch('/api/network/state');
    $('netcfg-renderer').textContent = `Gestor: ${state.renderer}` +
      (state.cloud_init ? ' · cloud-init detectado' : '') +
      (state.supported ? '' : ' · no soportado');
    renderNetworkInterfaces(state);
    if (state.available) {
      $('netcfg-state').textContent =
        `Puerta de enlace: ${state.gateway || '—'} · Interfaz predeterminada: ${state.default_interface || '—'}`;
      if (state.default_interface && !$('netcfg-iface').value) $('netcfg-iface').value = state.default_interface;
      if (state.gateway && !$('netcfg-gateway').value) $('netcfg-gateway').value = state.gateway;
    } else {
      $('netcfg-state').textContent = 'Estado de red no disponible en este sistema (requiere Linux).';
    }
  } catch (error) {
    $('netcfg-state').textContent = 'Error leyendo el estado: ' + error.message;
  }
}

/** Pinta la tabla de interfaces de la máquina (nombre, estado, IP/CIDR, máscara, MAC, MTU). */
function renderNetworkInterfaces(state) {
  const tbody = $('network-interfaces-table');
  if (!tbody) return;
  const summary = $('network-summary');

  if (!state.available) {
    if (summary) summary.textContent = '';
    tbody.innerHTML = '<tr><td colspan="6" class="empty-row">Estado de red no disponible en este sistema (requiere Linux).</td></tr>';
    return;
  }

  const interfaces = state.interfaces || [];
  if (summary) {
    summary.textContent =
      `${interfaces.length} interfaz(es) · Puerta de enlace: ${state.gateway || '—'} · Gestor: ${state.renderer}` +
      (state.cloud_init ? ' · cloud-init detectado' : '');
  }

  if (interfaces.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-row">No se detectaron interfaces.</td></tr>';
    return;
  }

  tbody.innerHTML = interfaces
    .map((iface) => {
      const isDefault = state.default_interface && iface.name === state.default_interface;
      const addresses = (iface.cidrs && iface.cidrs.length ? iface.cidrs : (iface.addresses || []));
      const masks = iface.netmasks || [];
      return `
        <tr>
          <td>
            <code class="text-cyan-300">${escapeHtml(iface.name)}</code>
            ${isDefault ? ' <span class="badge badge-running">predeterminada</span>' : ''}
          </td>
          <td>${iface.up
            ? '<span class="badge badge-running">up</span>'
            : '<span class="badge badge-stopped">down</span>'}</td>
          <td>${escapeHtml(addresses.join(', ') || 'sin IP')}</td>
          <td class="text-slate-400">${escapeHtml(masks.join(', ') || '—')}</td>
          <td class="text-slate-400">${escapeHtml(iface.mac || '—')}</td>
          <td class="text-slate-400">${iface.mtu == null ? '—' : escapeHtml(String(iface.mtu))}</td>
        </tr>`;
    })
    .join('');
}

function collectNetworkPlan() {
  return {
    interface: $('netcfg-iface').value.trim(),
    address: $('netcfg-address').value.trim(),
    prefix: Number($('netcfg-prefix').value || 24),
    gateway: $('netcfg-gateway').value.trim(),
    dns: $('netcfg-dns').value.split(',').map((item) => item.trim()).filter(Boolean)
  };
}

async function planNetworkChange() {
  try {
    const result = await apiFetch('/api/network/plan', {
      method: 'POST',
      body: JSON.stringify(collectNetworkPlan())
    });
    $('netcfg-preview').textContent = result.preview || '';
    const extra = (result.warnings || []).length ? ' · ' + result.warnings.join(' ') : '';
    setNetStatus(result.message + extra, result.ok ? 'ok' : 'error');
  } catch (error) {
    setNetStatus('Error al previsualizar: ' + error.message, 'error');
  }
}

async function applyNetworkChange() {
  if (!window.confirm('¿Aplicar el cambio de IP? Tendrás 90 segundos para confirmar desde la NUEVA dirección; si no, la red se revierte sola.')) return;
  try {
    const result = await apiFetch('/api/network/apply', {
      method: 'POST',
      body: JSON.stringify(collectNetworkPlan())
    });
    $('netcfg-preview').textContent = result.preview || '';
    setNetStatus(result.message, result.success ? 'ok' : 'error');
    if (result.success && result.token) startNetCountdown(result.token, result.ttl || 90);
  } catch (error) {
    setNetStatus('Error al aplicar: ' + error.message, 'error');
  }
}

function startNetCountdown(token, ttl) {
  netcfgToken = token;
  const box = $('netcfg-confirm');
  box.classList.remove('hidden');
  const deadline = Date.now() + ttl * 1000;
  const newAddress = $('netcfg-address').value.trim();
  const tick = () => {
    const remaining = Math.max(0, Math.round((deadline - Date.now()) / 1000));
    $('netcfg-countdown').textContent = remaining > 0
      ? `Cambio aplicado. Abre el panel en la NUEVA dirección (http://${newAddress}:8000) y confirma en ${remaining} s, o la red se revertirá automáticamente.`
      : 'Tiempo agotado: la red se está revirtiendo automáticamente.';
    if (remaining <= 0 && netcfgTimer) {
      window.clearInterval(netcfgTimer);
      netcfgTimer = null;
    }
  };
  tick();
  if (netcfgTimer) window.clearInterval(netcfgTimer);
  netcfgTimer = window.setInterval(tick, 1000);
}

async function confirmNetworkChange() {
  if (!netcfgToken) return;
  try {
    const result = await apiFetch('/api/network/confirm', {
      method: 'POST',
      body: JSON.stringify({ token: netcfgToken })
    });
    showToast(result.message, result.success ? 'success' : 'error');
    if (result.success) {
      $('netcfg-confirm').classList.add('hidden');
      if (netcfgTimer) window.clearInterval(netcfgTimer);
      netcfgTimer = null;
      netcfgToken = null;
      fetchNetworkState();
    }
  } catch (error) {
    showToast('Error al confirmar: ' + error.message, 'error');
  }
}

async function revertNetworkChange() {
  if (!netcfgToken) return;
  try {
    const result = await apiFetch('/api/network/revert', {
      method: 'POST',
      body: JSON.stringify({ token: netcfgToken })
    });
    showToast(result.message, result.success ? 'success' : 'error');
    if (result.success) {
      $('netcfg-confirm').classList.add('hidden');
      if (netcfgTimer) window.clearInterval(netcfgTimer);
      netcfgTimer = null;
      netcfgToken = null;
      fetchNetworkState();
    }
  } catch (error) {
    showToast('Error al revertir: ' + error.message, 'error');
  }
}

// ============ Propuestas de la IA con confirmación (Fase 5) ============
function renderPendingActions(pendingActions) {
  if (!pendingActions || pendingActions.length === 0) return;
  const container = $('chat-messages');
  if (!container) return;
  pendingActions.forEach((action) => {
    const card = document.createElement('div');
    card.className = 'chat-bubble assistant';
    card.innerHTML = `
      <p class="text-xs font-semibold">Propuesta pendiente de confirmación</p>
      <p class="mt-1 text-sm">${escapeHtml(action.summary)}</p>
      <p class="mt-1 text-xs text-slate-400">Caduca: ${escapeHtml(action.expires_at)}</p>
      <div class="mt-2 flex flex-wrap gap-2">
        <button class="btn-primary text-xs" data-action-confirm="${escapeHtml(action.id)}">Confirmar y ejecutar</button>
        <button class="btn-ghost text-xs" data-action-cancel="${escapeHtml(action.id)}">Cancelar</button>
      </div>`;
    container.appendChild(card);
    card.querySelector('[data-action-confirm]').addEventListener('click', () => confirmPendingAction(action.id, card));
    card.querySelector('[data-action-cancel]').addEventListener('click', () => cancelPendingAction(action.id, card));
  });
  container.scrollTop = container.scrollHeight;
}

async function confirmPendingAction(actionId, card) {
  try {
    const result = await apiFetch(`/api/chat/actions/${encodeURIComponent(actionId)}/confirm`, { method: 'POST' });
    showToast(result.message, result.success ? 'success' : 'error');
    card.style.opacity = '0.6';
    fetchServices();
  } catch (error) {
    showToast('No se pudo confirmar: ' + error.message, 'error');
  }
}

async function cancelPendingAction(actionId, card) {
  try {
    const result = await apiFetch(`/api/chat/actions/${encodeURIComponent(actionId)}/cancel`, { method: 'POST' });
    showToast(result.message, result.success ? 'success' : 'error');
    card.style.opacity = '0.6';
  } catch (error) {
    showToast('No se pudo cancelar: ' + error.message, 'error');
  }
}

// ============================ Navegación ============================
function showTab(name) {
  document.querySelectorAll('.tab-section').forEach((section) => section.classList.add('hidden'));

  if (name === 'chat') {
    const chatPanel = $('chat-panel');
    if (chatPanel) chatPanel.classList.remove('hidden');
  } else {
    const section = $(`section-${name}`);
    if (section) section.classList.remove('hidden');
    // Al abrir la pestaña de servicios se refresca el inventario.
    if (name === 'services') fetchServices();
    // Al abrir la pestaña de red se refresca el estado de red.
    if (name === 'network') fetchNetworkState();
    // El panel de chat permanece visible solo en pantallas grandes (lg:block).
    const chatPanel = $('chat-panel');
    if (chatPanel) chatPanel.classList.add('hidden');
  }

  document.querySelectorAll('.tab-btn').forEach((button) => {
    button.classList.toggle('tab-active', button.dataset.tab === name);
  });
}

// ============================== Gráfica ==============================
function initChart() {
  const canvas = $('resource-chart');
  if (!canvas || typeof Chart === 'undefined') return;

  state.chart = new Chart(canvas, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        {
          label: 'CPU',
          data: [],
          borderColor: '#4da6ff',
          backgroundColor: 'rgba(77, 166, 255, 0.14)',
          fill: true,
          tension: 0.35,
          pointRadius: 0,
          borderWidth: 2
        },
        {
          label: 'RAM',
          data: [],
          borderColor: '#9d8cff',
          backgroundColor: 'rgba(157, 140, 255, 0.12)',
          fill: true,
          tension: 0.35,
          pointRadius: 0,
          borderWidth: 2
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { intersect: false, mode: 'index' },
      scales: {
        y: {
          min: 0,
          max: 100,
          ticks: { color: '#8497ba', callback: (value) => `${value}%` },
          grid: { color: 'rgba(233, 241, 253, 0.08)' }
        },
        x: {
          ticks: { color: '#8497ba', maxTicksLimit: 6 },
          grid: { display: false }
        }
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#0d1527',
          titleColor: '#e9f1fd',
          bodyColor: '#b9c7e2',
          borderColor: '#1a2740',
          borderWidth: 1,
          callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y}%` }
        }
      }
    }
  });
}

// ===================== Sistema visual ambiental =====================
/* Motor del sistema portado de la variante local: el puntero que
   alimenta el foco de .nx-ambient::after y el revelado de los bloques
   [data-reveal] al entrar en pantalla.
   Es el MISMO bloque que login.html y settings.html repiten en línea:
   esas dos páginas no cargan app.js porque su arranque es exclusivo del
   panel, así que el motor tiene que llegarles sin salir del <body>. */

// Publica la posición del puntero como --mx / --my en el elemento raíz
// para que el foco de .nx-ambient::after quede justo debajo del cursor.
// Un solo listener pasivo, sin rAF ni lecturas de layout: por cuadro solo
// escribe dos propiedades. La capa que pinta es fija respecto a la
// ventana, así que las coordenadas de cliente son el espacio correcto y
// el foco no se desplaza al hacer scroll. Los valores por defecto del CSS
// cubren el primer pintado, el táctil y el movimiento reducido —donde la
// capa se apaga—, por eso el listener puede quedarse puesto siempre.
function initAmbientPointer() {
  window.addEventListener(
    'pointermove',
    (event) => {
      document.documentElement.style.setProperty('--mx', `${event.clientX}px`);
      document.documentElement.style.setProperty('--my', `${event.clientY}px`);
    },
    { passive: true }
  );
}

// Revela los bloques [data-reveal] una sola vez, con cascada entre
// hermanos. El estado oculto lo pone el CSS y solo si el <html> lleva la
// clase `js`, así que sin JavaScript la página se ve entera.
function initScrollReveal() {
  const items = Array.from(document.querySelectorAll('[data-reveal]'));
  if (items.length === 0) return;

  // Sin IntersectionObserver no hay forma de detectar la entrada en
  // pantalla: se muestran todos de golpe en lugar de dejar nada invisible.
  if (!('IntersectionObserver' in window)) {
    items.forEach((item) => {
      item.classList.add('is-visible');
    });
    return;
  }

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Cascada: los hermanos se revelan en orden de documento, con tope de
  // 6 pasos (0…5) para que una lista larga no espere a una cascada lenta.
  if (!reducedMotion) {
    const counts = Object.create(null);
    items.forEach((item) => {
      const parentKey = item.parentNode ? item.parentNode.nodeName : 'root';
      const index = counts[parentKey] === undefined ? 0 : counts[parentKey];
      counts[parentKey] = index + 1;
      item.style.setProperty('--reveal-delay', `${Math.min(index, 5) * 80}ms`);
    });
  }

  // Con movimiento reducido el mismo observador solo pone la clase: el
  // desplazamiento lo quita el CSS y el fundido se queda.
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('is-visible');
        observer.unobserve(entry.target);
      });
    },
    reducedMotion ? { threshold: 0.2 } : { threshold: 0.2, rootMargin: '0px 0px -8% 0px' }
  );

  // Las secciones de pestaña empiezan en display:none: nunca se solapan
  // con el observador, así que se revelan solas cuando la pestaña las
  // muestra. No hace falta observarlas de nuevo.
  items.forEach((item) => {
    observer.observe(item);
  });
}

// ============================ Arranque ============================
function startPolling() {
  if (state.pollTimer) window.clearInterval(state.pollTimer);
  state.pollTimer = window.setInterval(() => {
    // Se pausa el sondeo cuando la pestaña no está visible (ahorro en la Pi).
    if (document.visibilityState !== 'visible') return;
    fetchMetrics();
    fetchContainers();
  }, CONFIG.POLL_INTERVAL_MS);
}

function registerEventListeners() {
  document.querySelectorAll('.tab-btn').forEach((button) => {
    button.addEventListener('click', () => showTab(button.dataset.tab));
  });

  $('deploy-form').addEventListener('submit', deployContainer);
  $('refresh-containers').addEventListener('click', fetchContainers);
  $('scan-form').addEventListener('submit', scanNetwork);
  $('chat-form').addEventListener('submit', sendChat);
  $('chat-clear').addEventListener('click', clearChat);

  const logoutButton = $('logout-btn');
  if (logoutButton) logoutButton.addEventListener('click', logout);

  const refreshServices = $('refresh-services');
  if (refreshServices) refreshServices.addEventListener('click', fetchServices);

  const servicesSearch = $('services-search');
  if (servicesSearch) {
    servicesSearch.addEventListener('input', () => {
      state.servicesFilter.query = servicesSearch.value;
      applyServicesFilter();
    });
  }
  const servicesState = $('services-state');
  if (servicesState) {
    servicesState.addEventListener('change', () => {
      state.servicesFilter.state = servicesState.value;
      applyServicesFilter();
    });
  }
  const servicesScope = $('services-scope');
  if (servicesScope) {
    servicesScope.addEventListener('change', () => {
      state.servicesFilter.scope = servicesScope.value;
      applyServicesFilter();
    });
  }

  const closeServiceDetail = $('service-detail-close');
  if (closeServiceDetail) {
    closeServiceDetail.addEventListener('click', () => {
      $('service-detail').classList.add('hidden');
    });
  }

  const closeConfig = $('config-close');
  if (closeConfig) {
    closeConfig.addEventListener('click', () => $('config-panel').classList.add('hidden'));
  }
  const configValidate = $('config-validate');
  if (configValidate) configValidate.addEventListener('click', validateConfig);
  const configSave = $('config-save');
  if (configSave) configSave.addEventListener('click', () => saveConfig(false));
  const configRestore = $('config-restore-toggle');
  if (configRestore) {
    configRestore.addEventListener('click', () => {
      if (configState.service) loadBackups(configState.service);
    });
  }

  const netPlan = $('netcfg-plan');
  if (netPlan) netPlan.addEventListener('click', planNetworkChange);
  const netRefresh = $('refresh-network-state');
  if (netRefresh) netRefresh.addEventListener('click', fetchNetworkState);
  const netApply = $('netcfg-apply');
  if (netApply) netApply.addEventListener('click', applyNetworkChange);
  const netConfirm = $('netcfg-confirm-btn');
  if (netConfirm) netConfirm.addEventListener('click', confirmNetworkChange);
  const netRevert = $('netcfg-revert-btn');
  if (netRevert) netRevert.addEventListener('click', revertNetworkChange);

  // Atajo: Enter envía el chat (Shift+Enter inserta salto de línea).
  $('chat-input').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      $('chat-form').requestSubmit();
    }
  });

  // Al volver a la pestaña, refresca de inmediato.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      fetchMetrics();
      fetchContainers();
    }
  });
}

async function initDashboard() {
  // El sistema visual arranca de inmediato, antes de esperar a la sesión:
  // si el arranque dependiera de la red, la página se quedaría sin fondo
  // iluminado mientras el backend responde.
  initAmbientPointer();
  initScrollReveal();

  // La sesión se valida primero: si no es válida, apiFetch redirige al login.
  try {
    await fetchSession();
  } catch (_error) {
    // El 401 ya provoca la redirección; otros fallos se reflejan en el estado.
  }
  initChart();
  registerEventListeners();
  await checkHealth();
  await Promise.all([fetchMetrics(), fetchContainers()]);
  startPolling();
}

document.addEventListener('DOMContentLoaded', initDashboard);
