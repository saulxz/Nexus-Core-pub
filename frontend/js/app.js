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
  sendingChat: false
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
    const response = await fetch(`${CONFIG.API_BASE}${path}`, {
      ...options,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }
    });
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
    dot.className = 'h-2.5 w-2.5 rounded-full bg-emerald-400 shadow-glow';
    text.textContent = 'En línea';
  } else {
    dot.className = 'h-2.5 w-2.5 rounded-full bg-red-500';
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

function showTypingIndicator() {
  const container = $('chat-messages');
  if (!container) return null;
  const indicator = document.createElement('div');
  indicator.className = 'chat-bubble assistant';
  indicator.id = 'typing-indicator';
  indicator.innerHTML = '<span class="typing"><span></span><span></span><span></span></span>';
  container.appendChild(indicator);
  container.scrollTop = container.scrollHeight;
  return indicator;
}

function removeTypingIndicator() {
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

    // Notifica si la IA ejecutó alguna acción sobre el sistema.
    if (result.actions && result.actions.length > 0) {
      showToast(`El agente ejecutó: ${result.actions.map((a) => a.tool).join(', ')}`, 'success');
      // Refresca las vistas afectadas para reflejar los cambios.
      fetchMetrics();
      fetchContainers();
    }
  } catch (error) {
    removeTypingIndicator();
    appendChatMessage('assistant', `⚠️ ${error.message}`);
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

// ============================ Navegación ============================
function showTab(name) {
  document.querySelectorAll('.tab-section').forEach((section) => section.classList.add('hidden'));

  if (name === 'chat') {
    const chatPanel = $('chat-panel');
    if (chatPanel) chatPanel.classList.remove('hidden');
  } else {
    const section = $(`section-${name}`);
    if (section) section.classList.remove('hidden');
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
          borderColor: '#22d3ee',
          backgroundColor: 'rgba(34, 211, 238, 0.12)',
          fill: true,
          tension: 0.35,
          pointRadius: 0,
          borderWidth: 2
        },
        {
          label: 'RAM',
          data: [],
          borderColor: '#34d399',
          backgroundColor: 'rgba(52, 211, 153, 0.10)',
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
          ticks: { color: '#64748b', callback: (value) => `${value}%` },
          grid: { color: 'rgba(51, 65, 85, 0.4)' }
        },
        x: {
          ticks: { color: '#64748b', maxTicksLimit: 6 },
          grid: { display: false }
        }
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#0f172a',
          borderColor: '#334155',
          borderWidth: 1,
          callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y}%` }
        }
      }
    }
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
  initChart();
  registerEventListeners();
  await checkHealth();
  await Promise.all([fetchMetrics(), fetchContainers()]);
  startPolling();
}

document.addEventListener('DOMContentLoaded', initDashboard);
