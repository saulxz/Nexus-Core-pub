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
  HISTORY_POINTS: 60,           // Muestras en memoria por sensor (mini-gráfico).
  ALARM_LIMIT: 50,              // Alarmas retenidas en la sesión.
  REQUEST_TIMEOUT_MS: 30000,    // Timeout para peticiones normales.
  CHAT_TIMEOUT_MS: 120000       // El agente con IA puede tardar más.
};

// ============================== Estado ==============================
const state = {
  chart: null,
  pollTimer: null,
  clockTimer: null,             // Tick de 1 s: cuenta atrás, frescura y horas relativas.
  pollPaused: false,            // Pausa manual del sondeo (misma vida que la página).
  nextPollAt: 0,                // Marca de tiempo de la próxima lectura programada.
  chatHistory: [],              // [{ role, content }]
  sendingChat: false,
  csrf: '',                     // Token CSRF de la sesión (cabecera X-CSRF-Token).
  username: '',                 // Usuario autenticado.
  services: [],                 // Último inventario recibido.
  servicesFilter: { query: '', state: 'all', scope: 'all' },

  // --- Capa de monitorización ---
  sensorStatus: {},             // { [clave]: 'ok' | 'warn' | 'crit' | 'unknown' }
  sensorValue: {},              // { [clave]: número | null }
  freshAt: {},                  // { [clave]: marca de la última muestra }
  history: {},                  // { [clave]: [números] } — ventana en memoria
  alarms: [],                   // Sesión en curso, la más reciente primero.
  alarmSeq: 0,
  alarmFilter: 'all',
  containerStates: {},          // { [id]: { state, name } }
  containerStatus: {},          // { [id]: severidad derivada }
  linkStatus: undefined,        // 'ok' mientras el backend responde.

  // --- Vistas de alertas, histórico, monitores, auditoría, sistema
  //     y seguridad. Cada bloque se carga la primera vez que se abre su
  //     pestaña, y `loaded` evita repetir la llamada al volver atrás. ---
  alertRules: [],
  alertEvents: [],
  eventFilter: 'all',
  channels: [],
  windows: [],
  proposals: [],
  editingRuleId: null,
  editingChannelId: null,
  editingWindowId: null,
  monitors: [],
  editingMonitorId: null,
  openMonitorId: null,
  heartbeatHours: 24,
  audit: { limit: 50, offset: 0, action: '', since: '', total: 0 },
  twoFactor: null,
  twoFactorSecret: null,
  twoFactorRecovery: null,
  containerDetailId: null,
  historyChart: null,
  historyRange: '24h',
  historyMetrics: ['cpu', 'ram'],
  historyPayload: null,
  heartbeatsChart: null,

  // --- Flota de hosts (selección global) ---
  hosts: [],                    // Inventario de GET /api/hosts.
  hostId: 1,                    // Host elegido (localStorage).
  host: null,                   // Fila del inventario seleccionada.
  editingHostId: null,          // Host abierto en el formulario de edición.
  enrollment: null,             // Última respuesta de GET /api/hosts/enrollment.
  remoteSensorStatus: {},       // { [clave]: severidad } del host remoto.
  remoteFreshAt: 0,             // ts (ms) del último punto pintado del agente.
  remoteSeries: {},             // { [clave]: [números] } — minigráficos remotos.
  localSnapshot: null,          // último /api/metrics, para volver a local al vuelo.
  terminalTargets: [],          // Hosts conectados con destino SSH disponible.
  terminalHostId: null,         // Host elegido para la pestaña Terminal.
  loaded: {}                    // { [clave]: true } — una vez por bloque
};


// ========================= Utilidades DOM ==========================
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

/**
 * Estado vacío/erróneo de una tabla, ya maquetado: una etiqueta breve
 * arriba y la explicación debajo. Antes se volcaba el texto crudo del
 * error dentro de la celda; ahora la fila respira y el motivo se lee.
 * Contrato: el <td> conserva la clase .empty-row que aporta styles.css.
 */
function emptyCell(span, label, text, isError = false) {
  return `<tr><td colspan="${span}" class="empty-row"><span class="nx-empty${isError ? ' nx-empty--error' : ''}">` +
    `<span class="nx-empty-label">${escapeHtml(label)}</span>` +
    `<span class="nx-empty-text">${escapeHtml(text)}</span>` +
    '</span></td></tr>';
}

/**
 * Pastilla de estado: LED + etiqueta legible.
 * `kind` es el nombre del tono. Los llamantes históricos pasan el
 * nombre CON prefijo ('badge-running'); aquí se normaliza, porque sin
 * esto se emitía `class="badge badge-badge-running"` y ninguna pastilla
 * del panel —contenedores, red o servicios— recibía su color de estado.
 */
function badgeHtml(kind, label) {
  const raw = String(kind || '');
  const tone = raw.startsWith('badge-') ? raw.slice(6) : raw;
  return `<span class="badge badge-${tone}"><i class="badge-led" aria-hidden="true"></i>${escapeHtml(label)}</span>`;
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

/**
 * Traduce el `detail` de un error del backend a una frase legible.
 * FastAPI devuelve una CADENA para los HTTPException (`{"detail":
 * "Regla no encontrada: 7"}`) pero una LISTA de objetos para los 422
 * de validación. Concatenar esa lista daba un texto con
 * "[object Object]" en medio, que es justo el error que más necesita
 * leerse: el 422 dice QUÉ campo está mal.
 */
function formatApiDetail(detail) {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      if (!item || typeof item !== 'object') return String(item);
      // `body` no aporta: el formulario ya sabe qué campo es cuál.
      const loc = Array.isArray(item.loc)
        ? item.loc.filter((part) => part !== 'body' && part !== 'query').join('.')
        : '';
      const message = item.msg || item.type || 'valor no válido';
      return loc ? `${loc}: ${message}` : message;
    }).join(' · ');
  }
  if (detail && typeof detail === 'object') return JSON.stringify(detail);
  return String(detail);
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
      throw new Error(formatApiDetail(detail));
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

/**
 * Ejecuta una herramienta del agente en el host seleccionado a través del
 * despachador del hub. `result` es lo que devolvió la herramienta; si el
 * agente contestó `status=error` se lanza el mensaje para que la vista lo
 * pinte como error y no como dato vacío.
 */
async function hostCommand(tool, args = {}, timeoutSeconds = 30) {
  const payload = await apiFetch(
    `/api/hosts/${encodeURIComponent(String(state.hostId))}/command`,
    { method: 'POST', body: JSON.stringify({ tool, args, timeout_s: timeoutSeconds }) },
    (timeoutSeconds + 15) * 1000
  );
  if (payload.status === 'error') {
    throw new Error(payload.error || 'El agente devolvió un error.');
  }
  if (payload.status !== 'done') {
    throw new Error(`El comando terminó en estado «${payload.status || 'desconocido'}».`);
  }
  return payload.result;
}

/** ¿La sección de esa pestaña está a la vista ahora mismo? */
function isTabVisible(name) {
  const section = document.getElementById(`section-${name}`);
  return Boolean(section && !section.classList.contains('hidden'));
}

// ==========================================================
// Capa de monitorización
// ------------------------------------------------------------
// El panel ya pedía /api/metrics y /api/containers cada 5 s. Este
// módulo no añade ni una petición: lee esas mismas respuestas y les
// da un significado de estado —el que en PRTG es "up / down / paused"
// y en NinjaOne es "healthy / needs attention / unhealthy"— con
// cinco posiciones y una sola regla: el color dice el estado, nunca
// decora.
// ==========================================================

/**
 * Tabla de umbrales. Un solo sitio decide qué es "aviso" y qué es
 * "crítico", y de ahí salen a la vez el LED, el color de la barra, el
 * veredicto global y los textos de alarma. Si algún día hay que
 * ajustarlos, se ajustan aquí y en ningún otro sitio.
 *
 * `scaleMax` es solo la escala del mini-gráfico: no cambia ningún
 * umbral. `perCore` marca la carga, que se normaliza por núcleos
 * porque en una Pi de 4 núcleos una carga de 4.0 no es "algo alta",
 * es la cola entera.
 */
const SENSORS = [
  { key: 'cpu',  label: 'CPU',         object: 'CPU',            warn: 80,   crit: 92,   digits: 1, unit: '%',  scaleMax: 100 },
  { key: 'ram',  label: 'RAM',         object: 'RAM',            warn: 80,   crit: 92,   digits: 1, unit: '%',  scaleMax: 100 },
  { key: 'swap', label: 'Swap',        object: 'Swap',           warn: 25,   crit: 60,   digits: 1, unit: '%',  scaleMax: 100 },
  { key: 'disk', label: 'Disco raíz',  object: 'Disco raíz',     warn: 80,   crit: 90,   digits: 1, unit: '%',  scaleMax: 100 },
  { key: 'load', label: 'Carga 1m',    object: 'Carga del host', warn: 0.7,  crit: 1.0,  digits: 2, unit: '',   scaleMax: 2, perCore: true },
  { key: 'temp', label: 'Temperatura', object: 'Temperatura',    warn: 70,   crit: 80,   digits: 1, unit: '°C', scaleMin: 20, scaleMax: 90 }
];
const SENSOR_BY_KEY = SENSORS.reduce((map, sensor) => {
  map[sensor.key] = sensor;
  return map;
}, Object.create(null));

/** Traduce una severidad a las clases que pinta styles.css. */
const LED_CLASS = {
  ok: 'nx-led--ok',
  warn: 'nx-led--warn',
  crit: 'nx-led--crit',
  unknown: 'nx-led--unknown',
  paused: 'nx-led--paused'
};
// `warn` y `danger` son los nombres de contrato de .progress-fill; el
// mapeo pasa por ellos para no dejar dos vocabularios paralelos.
const GAUGE_CLASS = { ok: 'ok', warn: 'warn', crit: 'danger', unknown: 'unknown' };
const SEVERITY_LABEL = { crit: 'Crítico', warn: 'Aviso', ok: 'Recuperado' };
/* Texto corto de la columna "Estado" de la lista de sensores: la misma
   información que da el LED, en palabras, como hace PRTG en su columna
   de estado. Se escribe en `#state-<clave>` (el LED lo pone app.js en
   `#led-<clave>`), así que el contrato de ids no cambia. */
const SENSOR_STATE_LABEL = {
  ok: 'Correcto',
  warn: 'Aviso',
  crit: 'Crítico',
  unknown: 'Sin dato'
};
// "Zona" es femenino: los textos tienen que concordar con ella o
// suenan a plantilla. "umbral de aviso", "zona crítica", "zona correcta".
const ZONE_WORD = { crit: 'crítica', warn: 'de aviso', ok: 'correcta', unknown: 'sin dato' };
// Título del estado vacío según el filtro activo.
const ALARM_EMPTY_TITLE = {
  crit: 'Sin alarmas críticas',
  warn: 'Sin avisos',
  ok: 'Sin recuperaciones'
};

/** Posición de un sensor: sin dato no es "bien", es gris. */
function sensorStatus(sensor, value) {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'unknown';
  if (value >= sensor.crit) return 'crit';
  if (value >= sensor.warn) return 'warn';
  return 'ok';
}

/** Escribe un valor de sensor como lo leería una persona. */
function formatSensor(sensor, value) {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'sin dato';
  const text = Number(value).toFixed(sensor.digits);
  return sensor.unit ? `${text} ${sensor.unit}` : text;
}

/** "hace 40 s" / "hace 2 min" / "ahora". */
function formatRelative(timestamp) {
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (seconds < 5) return 'ahora';
  if (seconds < 60) return `hace ${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `hace ${minutes} min`;
  return `hace ${Math.floor(minutes / 60)} h`;
}

/** "1 crítico" / "3 críticos". El plural mal puesto es el tipo de
 *  errata que delata un panel generado a máquina. */
function plural(count, singular, pluralForm) {
  return count === 1 ? singular : pluralForm;
}


// ------------------- Mini-gráficos -------------------
// Un <polyline>, un <path> de área y un círculo: tres atributos por
// sensor y cinco segundos entre refrescos. El viewBox se estira al
// ancho de la celda, así que el trazo lleva non-scaling-stroke para
// que el grosor no dependa de cuánto mide la columna.
const SPARK_WIDTH = 120;
const SPARK_HEIGHT = 32;
const SPARK_PAD = 2;

function drawSpark(sensor, samples) {
  const line = $(`spark-${sensor.key}-line`);
  const area = $(`spark-${sensor.key}-area`);
  const dot = $(`spark-${sensor.key}-dot`);
  if (!line || !area || !dot) return;

  if (!samples || samples.length === 0) {
    line.setAttribute('points', '');
    area.setAttribute('d', '');
    dot.setAttribute('cx', '-10');
    dot.setAttribute('cy', '-10');
    return;
  }

  const low = sensor.scaleMin || 0;
  const span = (sensor.scaleMax || 100) - low || 1;
  const usable = SPARK_HEIGHT - SPARK_PAD * 2;
  const step = samples.length > 1 ? SPARK_WIDTH / (samples.length - 1) : SPARK_WIDTH;
  const coords = samples.map((value, index) => {
    const x = samples.length > 1 ? index * step : SPARK_WIDTH;
    const ratio = Math.max(0, Math.min(1, (value - low) / span));
    return `${x.toFixed(1)},${(SPARK_HEIGHT - SPARK_PAD - ratio * usable).toFixed(1)}`;
  });

  line.setAttribute('points', coords.join(' '));
  area.setAttribute('d', `M0,${SPARK_HEIGHT} L${coords.join(' L')} L${SPARK_WIDTH},${SPARK_HEIGHT} Z`);
  const last = coords[coords.length - 1].split(',');
  dot.setAttribute('cx', last[0]);
  dot.setAttribute('cy', last[1]);
}

/** Una muestra más en la ventana del sensor. `null` no se guarda. */
function pushHistory(key, value) {
  if (value === null || value === undefined || !Number.isFinite(value)) return;
  if (!state.history[key]) state.history[key] = [];
  const series = state.history[key];
  series.push(value);
  while (series.length > CONFIG.HISTORY_POINTS) series.shift();
}

// ------------------- Alarmas -------------------
/**
 * Registra un evento. La lista es de SESIÓN: vive en un array de la
 * página y se recorta a CONFIG.ALARM_LIMIT entradas. No hay backend
 * de histórico y no se promete lo que no existe.
 */
function pushAlarm(severity, object, message) {
  state.alarmSeq += 1;
  state.alarms.unshift({
    id: state.alarmSeq,
    at: Date.now(),
    severity,
    object,
    message
  });
  if (state.alarms.length > CONFIG.ALARM_LIMIT) state.alarms.length = CONFIG.ALARM_LIMIT;
  renderAlarms();
}

function alarmRowHtml(alarm) {
  return `<div class="nx-alarm nx-alarm--${alarm.severity}">
    <i class="nx-led ${LED_CLASS[alarm.severity]}" aria-hidden="true"></i>
    <div class="nx-alarm-body">
      <p class="nx-alarm-head">
        <span class="nx-alarm-sev">${SEVERITY_LABEL[alarm.severity]}</span>
        <span class="nx-alarm-obj">${escapeHtml(alarm.object)}</span>
      </p>
      <p class="nx-alarm-msg">${escapeHtml(alarm.message)}</p>
    </div>
    <span class="nx-alarm-time" data-at="${alarm.at}">${formatRelative(alarm.at)}</span>
  </div>`;
}

function emptyAlarmsHtml(filter) {
  const filtered = filter !== 'all';
  return `<div class="nx-alarms-empty">
    <span class="nx-led nx-led--ok" aria-hidden="true"></span>
    <p class="nx-alarms-empty-title">${filtered ? ALARM_EMPTY_TITLE[filter] : 'Sin alarmas — todo en orden'}</p>
    <p class="nx-alarms-empty-text">${
      filtered
        ? 'No hay ningún evento de esta severidad en la sesión. El filtro "Todas" los muestra todos.'
        : 'Ningún sensor ha cruzado sus umbrales, no se ha caído ningún contenedor y el enlace con el panel sigue en pie.'
    }</p>
  </div>`;
}

function renderAlarms() {
  const list = $('alarm-list');
  if (!list) return;

  const counts = { crit: 0, warn: 0, ok: 0 };
  state.alarms.forEach((alarm) => { counts[alarm.severity] += 1; });
  document.querySelectorAll('[data-alarm-count]').forEach((node) => {
    const total = counts[node.dataset.alarmCount];
    node.textContent = total > 0 ? ` ${total}` : '';
  });

  const rows = state.alarmFilter === 'all'
    ? state.alarms
    : state.alarms.filter((alarm) => alarm.severity === state.alarmFilter);

  list.innerHTML = rows.length === 0
    ? emptyAlarmsHtml(state.alarmFilter)
    : rows.map(alarmRowHtml).join('');
}

/** Las horas relativas envejecen solas: el tick de 1 s las reescribe
 *  sin volver a construir el HTML de la lista. */
function refreshAlarmTimes() {
  const nodes = document.querySelectorAll('#alarm-list [data-at]');
  for (let index = 0; index < nodes.length; index += 1) {
    nodes[index].textContent = formatRelative(Number(nodes[index].dataset.at));
  }
}

function applyAlarmFilter(filter, scrollToPanel) {
  state.alarmFilter = filter;
  document.querySelectorAll('[data-alarm-filter]').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.alarmFilter === filter));
  });
  renderAlarms();
  renderRollup();
  if (scrollToPanel) scrollToAlarms();
}

function scrollToAlarms() {
  const panel = $('alarms-panel');
  if (!panel) return;
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  panel.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' });
}

// ------------------- Cruces de umbral -------------------
/**
 * Compara el estado de este sensor con el de la muestra anterior.
 * Cuatro casos, y el primero es el que evita que el panel mienta:
 * entrar en zona mala genera alarma, salir de ella genera una
 * RECUPERACIÓN (severidad `ok`, no un aviso más), quedarse igual no
 * genera nada — sin eso, un sensor al 83 % escribiría una alarma cada
 * cinco segundos— y un sensor que YA está mal en la primera muestra
 * también genera, porque un disco al 91 % al abrir el panel es un
 * problema actual aunque nadie haya visto cómo llegaba ahí.
 */
function evaluateSensorTransition(sensor, value, status) {
  const previous = state.sensorStatus[sensor.key];
  state.sensorStatus[sensor.key] = status;
  state.sensorValue[sensor.key] = value;
  const shown = formatSensor(sensor, value);

  if (previous === undefined) {
    if (status === 'crit' || status === 'warn') {
      const limit = formatSensor(sensor, status === 'crit' ? sensor.crit : sensor.warn);
      pushAlarm(
        status,
        sensor.object,
        `${shown} ya estaba en zona ${ZONE_WORD[status]} al abrir el panel (umbral ${limit})`
      );
    }
    return;
  }

  if (previous === status) return;

  if (status === 'crit' || status === 'warn') {
    const limit = formatSensor(sensor, status === 'crit' ? sensor.crit : sensor.warn);
    pushAlarm(
      status,
      sensor.object,
      `${shown} supera el umbral ${ZONE_WORD[status]} (${limit})`
    );
    return;
  }

  // Bajada de zona. Si venía de "sin dato" no hay nada que celebrar.
  if (previous === 'crit' || previous === 'warn') {
    pushAlarm(
      'ok',
      sensor.object,
      `${shown} sale de zona ${ZONE_WORD[previous]} y queda en zona ${ZONE_WORD[status]}`
    );
  }
}


// ------------------- Contenedores -------------------
// Gravedad de cada estado de Docker. `created` y `removing` son
// estados transitorios previstos: se pintan en gris, pero no avisan.
const CONTAINER_SEVERITY = {
  running: 'ok',
  restarting: 'warn',
  paused: 'warn',
  exited: 'warn',
  dead: 'crit',
  created: 'unknown',
  removing: 'unknown'
};

function containerSeverity(rawState) {
  return CONTAINER_SEVERITY[rawState] || 'unknown';
}

function containerLabel(rawState) {
  const known = CONTAINER_STATE_LABELS[rawState];
  return known ? known[1] : (rawState || 'desconocido');
}

function evaluateContainers(containers) {
  const previous = state.containerStates;
  const next = Object.create(null);
  const nextStatus = Object.create(null);

  (containers || []).forEach((container) => {
    const rawState = String(container.state || container.status || '').toLowerCase();
    const severity = containerSeverity(rawState);
    const object = `Contenedor ${container.name}`;
    next[container.id] = { state: rawState, name: container.name };
    nextStatus[container.id] = severity;

    const before = previous[container.id];
    if (!before) {
      // Primera vez que lo vemos. Si ya venía roto, se dice: un panel
      // de alarmas que empieza vacío con un contenedor parado miente.
      if (severity === 'crit' || severity === 'warn') {
        pushAlarm(severity, object, `Está en ${containerLabel(rawState).toLowerCase()} desde que se abrió el panel.`);
      }
    } else if (before.state !== rawState) {
      const beforeSeverity = containerSeverity(before.state);
      const from = containerLabel(before.state).toLowerCase();
      const to = containerLabel(rawState).toLowerCase();
      if (severity === 'crit' || severity === 'warn') {
        pushAlarm(severity, object, `Pasó de ${from} a ${to}.`);
      } else if (beforeSeverity === 'crit' || beforeSeverity === 'warn') {
        pushAlarm('ok', object, `Vuelve a ${to}; estaba ${from}.`);
      }
    }
  });

  // Un contenedor que desaparece de la lista se ha eliminado o se ha
  // recreado con otra id. También es un evento que merece una línea.
  Object.keys(previous).forEach((id) => {
    if (next[id]) return;
    pushAlarm('warn', `Contenedor ${previous[id].name}`, 'Ha desaparecido del host (eliminado o recreado).');
  });

  state.containerStates = next;
  state.containerStatus = nextStatus;
}

// ------------------- Roll-up global -------------------
/**
 * Cuenta los objetos monitorizados por severidad: sensores,
 * contenedores y enlace. Devuelve también los NOMBRES de los críticos y de
 * los que están en aviso, porque un contador sin nombres obliga a ir a
 * buscarlos: el nombre es la mitad del valor de un semáforo.
 */
function computeRollup() {
  const counts = { ok: 0, warn: 0, crit: 0, unknown: 0 };
  const critical = [];
  const warning = [];

  // ÁMBITO. Con un host remoto seleccionado los contadores se
  // recalculan SOLO contra los seis sensores de ese agente, con los
  // umbrales de SENSORS: es el mismo criterio aplicado a otra
  // máquina, no una copia de los contadores locales. Los
  // contenedores y el enlace con el panel NO se cuentan porque son
  // del hub, y sumarlos aquí presentaría como remoto lo que es
  // local. Lo que sí entra es el latido del agente: si el host está
  // desconectado, no hay muestra fiable y eso es crítico de verdad.
  if (isRemoteScope()) {
    const host = state.host;
    SENSORS.forEach((sensor) => {
      const status = state.remoteSensorStatus[sensor.key];
      if (!status) return;
      counts[status] += 1;
      if (status === 'crit') critical.push(sensor.object);
      else if (status === 'warn') warning.push(sensor.object);
    });
    if (host && !host.online) {
      counts.crit += 1;
      critical.push(`Agente de ${host.name}`);
    }
    return { counts, critical, warning };
  }

  SENSORS.forEach((sensor) => {
    const status = state.sensorStatus[sensor.key];
    if (!status) return;
    counts[status] += 1;
    if (status === 'crit') critical.push(sensor.object);
    else if (status === 'warn') warning.push(sensor.object);
  });
  Object.keys(state.containerStatus).forEach((id) => {
    const status = state.containerStatus[id];
    counts[status] += 1;
    if (status === 'crit') critical.push(state.containerStates[id] ? state.containerStates[id].name : id);
    else if (status === 'warn') warning.push(state.containerStates[id] ? state.containerStates[id].name : id);
  });
  if (state.linkStatus) {
    counts[state.linkStatus] += 1;
    if (state.linkStatus === 'crit') critical.push('Enlace con el panel');
  }
  return { counts, critical, warning };
}

/**
 * Banda de estado global. El peor estado presente manda, no el
 * promedio: en PRTG y en NinjaOne un solo crítico convierte el
 * semáforo entero en rojo aunque los otros veinte sensores estén
 * verdes. El orden de los chips (OK · Atención · Crítico) y el del
 * recuento es el mismo, para que la vista se lea de izquierda a
 * derecha como se lee una frase.
 */
function renderRollup() {
  const { counts, critical, warning } = computeRollup();
  const statusMap = isRemoteScope() ? state.remoteSensorStatus : state.sensorStatus;
  const measured = SENSORS.some((sensor) => statusMap[sensor.key] !== undefined);

  ['ok', 'warn', 'crit'].forEach((severity) => {
    const value = $(`count-${severity}`);
    if (value) value.textContent = measured ? String(counts[severity]) : '—';
    const chip = $(`chip-${severity}`);
    if (!chip) return;
    chip.classList.toggle('is-zero', measured && counts[severity] === 0);
    chip.setAttribute('aria-pressed', String(state.alarmFilter === severity));
  });

  const bar = $('status-bar');
  if (bar) bar.classList.toggle('nx-statusbar--allclear', measured && counts.crit === 0 && counts.warn === 0);

  const led = $('status-verdict-led');
  const title = $('status-verdict-title');
  const sub = $('status-verdict-sub');
  if (!led || !title || !sub) return;

  if (!measured) {
    led.className = 'nx-led nx-led--idle';
    title.textContent = 'Comprobando el host';
    sub.textContent = 'Esperando la primera lectura de telemetría.';
    return;
  }

  const summary = `${counts.ok} OK · ${counts.warn} en aviso · ` +
    `${counts.crit} ${plural(counts.crit, 'crítico', 'críticos')}` +
    (counts.unknown > 0 ? ` · ${counts.unknown} sin dato` : '');

  if (counts.crit > 0) {
    led.className = `nx-led ${LED_CLASS.crit}`;
    title.textContent = `${counts.crit} ${plural(counts.crit, 'elemento crítico', 'elementos críticos')}`;
    // Con pocos nombres se enumeran; con muchos, el contador y ya.
    const named = critical.length <= 3 ? `${critical.join(', ')} · ` : '';
    sub.textContent = `${named}${summary}`;
  } else if (counts.warn > 0) {
    led.className = `nx-led ${LED_CLASS.warn}`;
    title.textContent = 'Todo dentro de lo previsto';
    // Mismo criterio que en la rama crítica: con pocos nombres se
    // enumeran; con muchos, el contador y ya.
    const named = warning.length <= 3 ? `${warning.join(', ')} · ` : '';
    sub.textContent = `${named}${summary}`;
  } else {
    led.className = `nx-led ${LED_CLASS.ok}`;
    title.textContent = 'Todo en orden';
    sub.textContent = summary;
  }
}


// ------------------- Muestreo: pausa y cuenta atrás -------------------
/**
 * Pinta la barra de estado del sondeo: cuánto falta, o que está parado.
 * El texto largo vive en un span aparte que el CSS oculta en pantalla
 * estrecha — "próximo muestreo en 4 s" se come 130px de la fila de
 * pestañas y deja la navegación en 100px.
 */
function renderCountdown() {
  const prefix = document.querySelector('.nx-poll-prefix');
  const seconds = $('poll-seconds');
  if (!seconds) return;
  if (prefix) prefix.hidden = state.pollPaused;
  seconds.textContent = state.pollPaused
    ? 'pausado'
    : `${Math.max(0, Math.ceil((state.nextPollAt - Date.now()) / 1000))} s`;
}


/** "hace 3 s" en cada tile. Es el dato que vuelve honesto un valor. */
function renderFreshness() {
  // Con un host remoto la marca de frescura es la del PUNTO que
  // trajo el agente, no la del momento en que se pintó: el reloj de
  // 1 Hz actualiza las dos sin volver a pedir nada.
  const clock = isRemoteScope() ? state.remoteFreshAt : null;
  SENSORS.forEach((sensor) => {
    const node = $(`fresh-${sensor.key}`);
    if (!node) return;
    const at = clock || state.freshAt[sensor.key];
    node.textContent = at ? formatRelative(at) : '—';
  });
}

/** Reloj de interfaz a 1 Hz. Solo escribe texto: nada de layout ni de reflow. */
function startUiClock() {
  if (state.clockTimer) window.clearInterval(state.clockTimer);
  state.clockTimer = window.setInterval(() => {
    renderCountdown();
    renderFreshness();
    refreshAlarmTimes();
  }, 1000);
}

function setPollingPaused(paused) {
  state.pollPaused = paused;
  const button = $('poll-toggle');
  if (button) {
    button.setAttribute('aria-pressed', String(paused));
    button.title = paused ? 'Reanudar el muestreo automático' : 'Pausar el muestreo automático';
  }
  const label = $('poll-toggle-label');
  if (label) label.textContent = paused ? 'Reanudar' : 'Pausar';

  if (!paused) {
    // Reanudar lee de inmediato: si no, el panel muestra durante 5 s
    // un "pausado" que ya no lo está.
    state.nextPollAt = Date.now() + CONFIG.POLL_INTERVAL_MS;
    fetchMetrics();
    fetchContainers();
  }
  renderCountdown();
}

// ========================= Estado de conexión =========================
function setOnlineStatus(isOnline) {
  const dot = $('status-dot');
  const text = $('status-text');
  const pill = $('live-pill');
  if (dot) dot.className = `nx-led ${isOnline ? 'nx-led--on' : 'nx-led--off'}`;
  if (text) text.textContent = isOnline ? 'En línea' : 'Sin conexión';
  if (pill) pill.className = `nx-live nx-live--${isOnline ? 'ok' : 'off'}`;

  // El enlace con el backend entra también en el roll-up: sin él, seis
  // sensores verdes sobre datos de hace un minuto serían mentira.
  const status = isOnline ? 'ok' : 'crit';
  const previous = state.linkStatus;
  state.linkStatus = status;
  if (previous !== undefined && previous !== status) {
    pushAlarm(
      status,
      'Enlace con el panel',
      isOnline
        ? 'El backend vuelve a responder.'
        : 'Se ha perdido la respuesta del backend; las métricas mostradas pueden estar congeladas.'
    );
  }
  renderRollup();
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
/**
 * Pinta la barra de un sensor: el ancho es cuánto lleva recorrido
 * hacia su umbral crítico y el color es la severidad actual. Se
 * conservan los nombres de clase históricos (`.warn`, `.danger`)
 * porque son contrato con styles.css; `.ok` y `.unknown` son las dos
 * posiciones que faltaban.
 */
function updateBar(barId, percent, status) {
  const bar = $(barId);
  if (!bar) return;
  const width = Math.max(0, Math.min(100, Number(percent) || 0));
  bar.style.width = `${width}%`;
  bar.classList.remove('ok', 'warn', 'danger', 'unknown');
  bar.classList.add(GAUGE_CLASS[status] || 'unknown');
}

/**
 * Actualiza una tile entera a partir de su valor: LED, barra,
 * mini-gráfico, frescura, alarma por cruce de umbral y roll-up.
 * `value` en `null` significa "sin dato" y deja el sensor en gris.
 */
function updateSensor(key, value) {
  const sensor = SENSOR_BY_KEY[key];
  if (!sensor) return;
  const hasValue = value !== null && value !== undefined && Number.isFinite(value);
  const numeric = hasValue ? Number(value) : null;
  const status = sensorStatus(sensor, numeric);

  const led = $(`led-${key}`);
  if (led) led.className = `nx-led ${LED_CLASS[status]}`;
  const stateCell = $(`state-${key}`);
  if (stateCell) {
    stateCell.textContent = SENSOR_STATE_LABEL[status] || SENSOR_STATE_LABEL.unknown;
    stateCell.className = `nx-sr-st nx-sr-st--${status}`;
  }
  const tile = document.querySelector(`.nx-sensor[data-sensor="${key}"]`);
  if (tile) tile.dataset.status = status;

  // La barra se llena al llegar al umbral crítico, no al 100 %: así
  // un 92 % de CPU y un 90 % de disco se leen igual de cerca del
  // borde, que es lo que importa.
  updateBar(`bar-${key}`, hasValue ? (numeric / sensor.crit) * 100 : 0, status);

  pushHistory(key, numeric);
  drawSpark(sensor, state.history[key]);
  state.freshAt[key] = Date.now();

  evaluateSensorTransition(sensor, numeric, status);
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

/**
 * Pinta la lectura local. Todo lo que ya se escribía aquí sigue
 * escribiéndose en los mismos ids; lo único que cambia es quién
 * llama: con un host remoto elegido, `renderMetrics` se queda en la
 * puerta (guarda la muestra y sigue sonando el muestreo) y esta
 * función no se ejecuta, de modo que ningún valor local puede
 * pisar la banda de sensores del agente.
 */
function paintLocalMetrics(metrics) {
  // Valores que el panel ya pintaba: se siguen escribiendo igual.
  $('metric-cpu').textContent = Number(metrics.cpu_percent).toFixed(1);
  $('metric-ram').textContent = Number(metrics.ram_percent).toFixed(1);
  $('metric-disk').textContent = Number(metrics.disk_percent).toFixed(1);

  // Los dos que /api/metrics ya enviaba y nadie leía: swap y carga.
  $('metric-swap').textContent = Number(metrics.swap_percent).toFixed(1);

  // La carga se normaliza por núcleos: con 4 núcleos, 1.00 significa
  // "un núcleo saturado", que es el umbral que se ha elegido.
  const loadAvg = Array.isArray(metrics.load_avg) ? metrics.load_avg : [];
  const cores = Math.max(1, Number(metrics.cpu_count) || 1);
  const loadRaw = Number(loadAvg[0]);
  const loadPerCore = Number.isFinite(loadRaw) ? loadRaw / cores : null;
  const loadValue = $('metric-load');
  if (loadValue) loadValue.textContent = loadPerCore === null ? '--' : loadPerCore.toFixed(2);
  const loadNote = $('metric-load-note');
  if (loadNote) {
    loadNote.textContent = loadPerCore === null
      ? 'Carga no disponible'
      : `Aviso 0.70 · crítico 1.00 · ${cores} ${cores === 1 ? 'núcleo' : 'núcleos'}`;
  }
  const loadFact = $('info-load');
  if (loadFact) {
    loadFact.textContent = loadAvg.length > 0
      ? loadAvg.slice(0, 3).map((value) => Number(value).toFixed(2)).join(' · ')
      : '—';
  }

  // Temperatura: puede no existir en un PC de desarrollo. "Sin sensor"
  // es un estado de la matriz, no un cero.
  const tempEl = $('metric-temp');
  const tempNote = $('metric-temp-note');
  const tempValue = metrics.cpu_temp_c === null || metrics.cpu_temp_c === undefined
    ? null
    : Number(metrics.cpu_temp_c);
  if (tempValue === null || !Number.isFinite(tempValue)) {
    tempEl.textContent = '--';
    tempNote.textContent = 'Sin sensor en este host';
    tempNote.className = 'nx-stat-foot';
  } else {
    tempEl.textContent = tempValue.toFixed(1);
    tempNote.textContent = 'Aviso 70 °C · crítico 80 °C';
    tempNote.className = 'nx-stat-foot';
  }

  // Información adicional del host.
  $('info-cpu-count').textContent = `${metrics.cpu_count}`;
  $('info-ram-used').textContent = formatMemory(metrics.ram_used_mb);
  $('info-uptime').textContent = formatUptime(metrics.uptime_seconds);

  // Estado: una pasada por sensor y una por el roll-up. Todo el trabajo
  // es O(sensores) sobre datos que ya estaban en memoria.
  updateSensor('cpu', Number(metrics.cpu_percent));
  updateSensor('ram', Number(metrics.ram_percent));
  updateSensor('swap', Number(metrics.swap_percent));
  updateSensor('disk', Number(metrics.disk_percent));
  updateSensor('load', loadPerCore);
  updateSensor('temp', tempValue);
  renderRollup();

  const updated = $('metrics-updated');
  if (updated) {
    updated.textContent = `Actualizado ${new Date().toLocaleTimeString('es-ES')}`;
  }
  state.nextPollAt = Date.now() + CONFIG.POLL_INTERVAL_MS;

  updateChart(metrics.cpu_percent, metrics.ram_percent);
}

/**
 * Puerta de entrada del muestreo. El sondeo de 5 s NO cambia nunca:
 * cuando el resumen está viendo un host remoto, la lectura local se
 * guarda en `state.localSnapshot` y no se pinta, para que volver al
 * host local sea instantáneo sin pedir nada.
 */
function renderMetrics(metrics) {
  state.localSnapshot = metrics;
  if (isRemoteScope()) {
    state.nextPollAt = Date.now() + CONFIG.POLL_INTERVAL_MS;
    return;
  }
  paintLocalMetrics(metrics);
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
// Estados de Docker traducidos a lenguaje llano. El valor crudo se
// conserva como respaldo para los estados poco habituales. La clase
// del badge sigue la gramática de cuatro estados del panel: verde
// sano, ámbar requiere atención, rojo crítico y gris sin dato —el
// gris nunca significa "bien", por eso un contenedor detenido se ve
// gris aunque su alarma sí sea un aviso.
const CONTAINER_STATE_LABELS = {
  running: ['badge-running', 'En ejecución'],
  created: ['badge-stopped', 'Creado'],
  paused: ['badge-paused', 'Pausado'],
  restarting: ['badge-warn', 'Reiniciando'],
  removing: ['badge-stopped', 'Eliminando'],
  exited: ['badge-stopped', 'Detenido'],
  dead: ['badge-error', 'Detenido con error']
};


function statusBadge(container) {
  const state = String(container.status || container.state || '').toLowerCase();
  // Cada rama saca TONO y ETIQUETA de la misma entrada de la tabla: si
  // el tono se escribía a mano, se desincronizaba del label y "paused"
  // acababa en gris (que significa "sin dato") en vez de en índigo.
  if (state.includes('exit')) return badgeHtml(CONTAINER_STATE_LABELS.exited[0], CONTAINER_STATE_LABELS.exited[1]);
  if (state.includes('dead')) return badgeHtml(CONTAINER_STATE_LABELS.dead[0], CONTAINER_STATE_LABELS.dead[1]);
  if (state.includes('pause')) return badgeHtml(CONTAINER_STATE_LABELS.paused[0], CONTAINER_STATE_LABELS.paused[1]);
  const known = CONTAINER_STATE_LABELS[state];
  if (known) return badgeHtml(known[0], known[1]);
  return badgeHtml('badge-stopped', state || 'Desconocido');
}


function renderContainers(containers) {
  const tbody = $('containers-table');
  if (!tbody) return;

  if (!containers || containers.length === 0) {
    tbody.innerHTML = emptyCell(6, 'Sin contenedores', 'Este host no tiene ninguna instancia de Docker en marcha.');
    return;
  }

  tbody.innerHTML = containers
    .map((container) => {
      const ports = (container.ports || []).map(escapeHtml).join(', ') || '—';
      return `
        <tr>
          <td class="nx-cell-id">${escapeHtml(container.short_id || container.id)}</td>
          <td class="nx-cell-strong">${escapeHtml(container.name)}</td>
          <td>${escapeHtml(container.image)}</td>
          <td>${statusBadge(container)}</td>
          <td class="nx-cell-num nx-cell-quiet">${ports}</td>
          <td class="nx-cell-actions">
            <span class="nx-btn-row">
              <button class="btn btn-ghost btn-sm" data-detail-id="${escapeHtml(container.id)}"
                      data-detail-name="${escapeHtml(container.name)}">Registros</button>
              <button class="btn btn-danger btn-sm" data-delete-id="${escapeHtml(container.id)}"
                      data-delete-name="${escapeHtml(container.name)}">Eliminar</button>
            </span>
          </td>
        </tr>`;
    })
    .join('');

  // Asocia los botones de la fila (delegación simple tras el render).
  tbody.querySelectorAll('[data-delete-id]').forEach((button) => {
    button.addEventListener('click', () =>
      deleteContainer(button.dataset.deleteId, button.dataset.deleteName)
    );
  });
  tbody.querySelectorAll('[data-detail-id]').forEach((button) => {
    button.addEventListener('click', () => openContainerDetail(button.dataset.detailId));
  });
}

async function fetchContainers() {
  if (isRemoteScope() && !isTabVisible('containers')) return;
  try {
    let containers;
    if (isRemoteScope()) {
      const result = await hostCommand('docker', { action: 'list' });
      containers = Array.isArray(result) ? result : result.containers || [];
    } else {
      const data = await apiFetch('/api/containers');
      containers = data.containers || [];
    }
    renderContainers(containers);
    evaluateContainers(containers);
    renderRollup();
  } catch (error) {
    console.error('Error listando contenedores:', error.message);
    const tbody = $('containers-table');
    if (tbody) {
      tbody.innerHTML = emptyCell(6, 'No se pudo leer Docker', error.message, true);
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
    const result = isRemoteScope()
      ? await hostCommand('docker', { action: 'deploy', ...payload }, 120)
      : await apiFetch('/api/containers', {
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
    const result = isRemoteScope()
      ? await hostCommand('docker', { action: 'remove', container_id: containerId, force: true })
      : await apiFetch(`/api/containers/${encodeURIComponent(containerId)}`, {
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
    tbody.innerHTML = emptyCell(4, 'Escaneo rechazado', result.error, true);
    if (summary) summary.textContent = 'Rechazado';
    return;
  }

  if (!result.hosts || result.hosts.length === 0) {
    tbody.innerHTML = emptyCell(4, 'Sin respuestas', 'Ningún host de la subred respondió al barrido.');
  } else {
    tbody.innerHTML = result.hosts
      .map(
        (host) => `
        <tr>
          <td class="nx-cell-id">${escapeHtml(host.ip)}</td>
          <td>${badgeHtml('badge-running', host.status)}</td>
          <td class="nx-cell-num nx-cell-quiet">${escapeHtml(host.mac || '—')}</td>
          <td class="nx-cell-strong">${escapeHtml(host.hostname || '—')}</td>
        </tr>`
      )
      .join('');
  }

  if (summary) {
    const time = new Date(result.scanned_at).toLocaleTimeString('es-ES');
    summary.textContent = `${result.count} hosts · ${time}`;
  }
}

async function scanNetwork(event) {
  event.preventDefault();
  const subnet = $('scan-subnet').value.trim();
  const submit = $('scan-submit');
  const tbody = $('network-table');

  submit.disabled = true;
  submit.innerHTML = '<span class="spinner"></span> Escaneando';
  tbody.innerHTML = emptyCell(4, 'Escaneando', 'Barrido de la subred en curso. Puede tardar unos segundos.');

  try {
    const payload = subnet ? { subnet } : {};
    const result = isRemoteScope()
      ? await hostCommand('network', { action: 'scan', ...payload }, 180)
      : await apiFetch('/api/network-scan', {
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
    tbody.innerHTML = emptyCell(4, 'El escaneo falló', error.message, true);
    showToast(`Error en el escaneo: ${error.message}`, 'error');
  } finally {
    submit.disabled = false;
    submit.textContent = 'Escanear red';
  }
}

// ========================== Servicios ==========================
// Estados de systemd en lenguaje llano; el valor crudo queda de apoyo.
// Misma gramática de cuatro estados que el resto del panel: un
// servicio inactivo es ámbar (existe, pero no hace su trabajo), no
// gris — el gris queda para "no hay dato", que aquí es un estado
// desconocido y no una unidad parada.
const SERVICE_STATE_LABELS = {
  active: ['badge-running', 'Activo'],
  reloading: ['badge-running', 'Recargando'],
  activating: ['badge-running', 'Activando'],
  deactivating: ['badge-running', 'Desactivando'],
  failed: ['badge-error', 'Fallido'],
  inactive: ['badge-warn', 'Inactivo'],
  dead: ['badge-error', 'Muerto']
};


function serviceBadge(activeState) {
  const stateValue = String(activeState || '').toLowerCase();
  const known = SERVICE_STATE_LABELS[stateValue];
  if (known) return badgeHtml(known[0], known[1]);
  return badgeHtml('badge-stopped', stateValue || 'Desconocido');
}

function renderServices(services) {
  state.services = services || [];
  applyServicesFilter();
}

function applyServicesFilter() {
  const tbody = $('services-table');
  if (!tbody) return;

  if (!state.services || state.services.length === 0) {
    tbody.innerHTML = emptyCell(5, 'Inventario vacío', 'systemd no ha devuelto ninguna unidad.');
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
      ? `${state.services.length} unidades`
      : `${filtered.length} de ${state.services.length} unidades`;
  }

  if (filtered.length === 0) {
    tbody.innerHTML = emptyCell(5, 'Sin coincidencias', 'Ningún servicio pasa los filtros actuales.');
    return;
  }

  tbody.innerHTML = filtered
    .map((service) => {
      const label = service.friendly_name
        ? `${service.friendly_name} · ${service.description}`
        : (service.description || '—');
      return `
        <tr>
          <td class="nx-cell-id">${escapeHtml(service.name)}</td>
          <td>${serviceBadge(service.active_state)}</td>
          <td class="nx-cell-num nx-cell-quiet">${escapeHtml(service.sub_state || '—')}</td>
          <td class="nx-cell-quiet">${escapeHtml(label)}</td>
          <td class="nx-cell-actions">
            <span class="nx-btn-row">${serviceActionsHtml(service.name, service.active_state)}</span>
          </td>
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
    const data = isRemoteScope()
      ? await hostCommand('services', { action: 'list' })
      : await apiFetch('/api/services');
    if (!data.available) {
      state.services = [];
      const counter = $('services-count');
      if (counter) counter.textContent = '';
      tbody.innerHTML = emptyCell(5, 'Inventario no disponible', data.reason || 'Este sistema no expone systemd.');
      return;
    }
    renderServices(data.services || []);
  } catch (error) {
    tbody.innerHTML = emptyCell(5, 'No se pudo leer systemd', error.message, true);
  }
}

function renderInstallCatalog(items) {
  const box = $('install-catalog');
  if (!box) return;
  if (!items || items.length === 0) {
    box.innerHTML = '<p class="nx-hint">El catálogo está vacío.</p>';
    return;
  }
  box.innerHTML = items.map((item) => {
    const installed = Boolean(item.installed);
    const active = item.active === true;
    const badge = installed ? (active ? 'badge-running' : 'badge-warn') : 'badge-stopped';
    const label = installed ? (active ? 'Activo' : 'Instalado') : 'No instalado';
    const packages = (item.packages || []).join(', ');
    return `
      <div class="nx-install-item">
        <div class="nx-install-info">
          <strong>${escapeHtml(item.name || item.key)}</strong>
          <p>${escapeHtml(item.description || '')}</p>
          <small>${escapeHtml(packages)}</small>
        </div>
        <span class="badge ${badge}">${label}</span>
        <button class="btn btn-primary btn-sm" type="button" data-install-key="${escapeHtml(item.key)}" ${installed ? 'disabled' : ''}>Instalar</button>
      </div>`;
  }).join('');
  box.querySelectorAll('[data-install-key]').forEach((button) => {
    button.addEventListener('click', () => installService(button.dataset.installKey));
  });
}

async function fetchInstallCatalog() {
  const box = $('install-catalog');
  const note = $('install-status');
  if (!box) return;
  box.innerHTML = '<p class="nx-hint">Cargando catálogo…</p>';
  if (note) note.textContent = '';
  try {
    const data = isRemoteScope()
      ? await hostCommand('install', { action: 'catalog' }, 60)
      : await apiFetch('/api/install/catalog');
    const items = Array.isArray(data) ? data : data.catalog || [];
    if (data && data.available === false && note) {
      note.textContent = data.reason || 'El instalador solo está disponible en Linux.';
    }
    renderInstallCatalog(items);
  } catch (error) {
    if (note) note.textContent = 'No se pudo cargar el catálogo: ' + error.message;
    box.innerHTML = '';
  }
}

async function installService(key) {
  if (!window.confirm(`¿Instalar «${key}» con apt? La acción queda auditada.`)) return;
  let button = null;
  try {
    button = document.querySelector(`[data-install-key="${key}"]`);
    if (button) {
      button.disabled = true;
      button.textContent = 'Instalando…';
    }
    const result = isRemoteScope()
      ? await hostCommand('install', { action: 'install', key }, 170)
      : await apiFetch(`/api/install/${encodeURIComponent(key)}`, {
          method: 'POST',
          body: JSON.stringify({ confirm: true }),
        }, 220000);
    const ok = Boolean(result.success);
    const message = result.message || result.reason || (ok ? 'Servicio instalado.' : 'No se pudo instalar.');
    showToast(message, ok ? 'success' : 'error');
    if (ok && result.restart_required) {
      showToast('Reinicia nexus o nexus-agent para aplicar el grupo docker.', 'success');
    }
    if (ok && isRemoteScope()) {
      // apt puede tardar más que la ventana de latido: espera a que el agente
      // vuelva a estar en línea antes de recargar el catálogo.
      await new Promise((resolve) => setTimeout(resolve, 6000));
    }
    await fetchInstallCatalog();
    await fetchServices();
  } catch (error) {
    showToast(`Error al instalar: ${error.message}`, 'error');
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = 'Instalar';
    }
  }
}

async function showServiceDetail(name) {
  const panel = $('service-detail');
  if (!panel) return;
  panel.classList.remove('hidden');
  $('service-detail-title').textContent = name;
  $('service-detail-body').innerHTML = '<p class="nx-hint">Cargando estado de la unidad…</p>';
  $('service-detail-logs').textContent = '';

  try {
    const encoded = encodeURIComponent(name);
    const [detail, logs] = isRemoteScope()
      ? await Promise.all([
        hostCommand('services', { action: 'detail', name }),
        hostCommand('services', { action: 'logs', name, lines: 100 })
      ])
      : await Promise.all([
        apiFetch(`/api/services/${encoded}`),
        apiFetch(`/api/services/${encoded}/logs?lines=100`)
      ]);
    const memory = detail.memory_bytes
      ? formatMemory(detail.memory_bytes / (1024 * 1024))
      : '—';
    const configService = configServiceFor(name);
    $('service-detail-body').innerHTML = `
      <div class="nx-kv">
        <div>
          <p class="nx-kv-label">Estado</p>
          <p class="nx-kv-value">${serviceBadge(detail.active_state)}</p>
        </div>
        <div>
          <p class="nx-kv-label">Subestado</p>
          <p class="nx-kv-value">${escapeHtml(detail.sub_state || '—')}</p>
        </div>
        <div>
          <p class="nx-kv-label">PID principal</p>
          <p class="nx-kv-value">${escapeHtml(detail.main_pid || '—')}</p>
        </div>
        <div>
          <p class="nx-kv-label">Memoria</p>
          <p class="nx-kv-value">${escapeHtml(memory)}</p>
        </div>
      </div>
      <p class="nx-t-4 nx-kv-value nx-kv-value--plain">${escapeHtml(detail.active_enter_timestamp || '—')}</p>
      <p class="nx-t-2 nx-hint">Última activación de la unidad</p>
      <p class="nx-t-3 nx-hint">${escapeHtml(detail.description || '')}</p>
      <p class="nx-t-2 nx-hint">Fichero de unidad: <code>${escapeHtml(detail.fragment_path || '—')}</code></p>
      <div class="nx-t-4 nx-hstack">
        ${serviceActionsHtml(name, detail.active_state)}
        ${configService && !isRemoteScope() ? `<button class="btn btn-ghost btn-sm" data-config-service="${configService}">Configurar</button>` : ''}
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
      : (logs.reason || 'Sin logs disponibles para esta unidad.');
  } catch (error) {
    $('service-detail-body').innerHTML = `<p class="nx-status nx-status--error">${escapeHtml(error.message)}</p>`;
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
    parts.push(`<button class="btn btn-ghost btn-sm" data-svc-action="restart" data-svc-unit="${name}">Reiniciar</button>`);
    parts.push(`<button class="btn btn-ghost btn-sm" data-svc-action="stop" data-svc-unit="${name}">Parar</button>`);
  } else {
    parts.push(`<button class="btn btn-ghost btn-sm" data-svc-action="start" data-svc-unit="${name}">Arrancar</button>`);
  }
  parts.push(`<button class="btn btn-ghost btn-sm" data-service-name="${name}">Detalle</button>`);
  return parts.join(' ');
}

async function serviceAction(unit, action) {
  const label = SERVICE_ACTION_LABELS[action] || action;
  if (!window.confirm(`¿${label} el servicio ${unit}?`)) return;
  try {
    const result = isRemoteScope()
      ? await hostCommand(
        'services',
        { action: 'action', name: unit, service_action: action, actor: 'panel' },
        60
      )
      : await apiFetch(`/api/services/${encodeURIComponent(unit)}/action`, {
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
  const suffix = kind === 'ok' ? ' nx-status--ok' : kind === 'error' ? ' nx-status--error' : '';
  box.textContent = message;
  box.className = `nx-status${suffix}`;
  box.classList.remove('hidden');
}

async function openServiceConfig(service) {
  const panel = $('config-panel');
  if (!panel) return;
  configState.service = service;
  panel.classList.remove('hidden');
  $('config-title').textContent = service === 'samba' ? 'Samba · smb.conf' : 'dnsmasq · DNS y DHCP';
  $('config-status').classList.add('hidden');
  $('config-preview').textContent = '';
  $('config-note').classList.add('hidden');
  $('config-form').innerHTML = '<p class="nx-hint">Cargando configuración…</p>';
  try {
    const data = await apiFetch(`/api/config/${service}`);
    if (!data.managed && data.exists) {
      $('config-note').textContent =
        'El fichero actual no lo gestiona el panel: al guardar se generará desde este formulario, siempre con copia de seguridad previa. ' +
        (data.reason || '');
      $('config-note').classList.remove('hidden');
    } else if (!data.exists) {
      $('config-note').textContent = `El fichero ${data.path} todavía no existe; se creará al guardar con esta configuración.`;
      $('config-note').classList.remove('hidden');
    }
    $('config-form').innerHTML =
      service === 'samba' ? sambaFormHtml(data.config) : dnsmasqFormHtml(data.config);
    bindConfigFormEvents();
    await loadBackups(service);
  } catch (error) {
    $('config-form').innerHTML = `<p class="nx-status nx-status--error">${escapeHtml(error.message)}</p>`;
  }
}

function sambaFormHtml(model) {
  const shares = model.shares || [];
  return `
    <div class="nx-grid-3">
      <label class="field">
        <span class="nx-eyebrow">Grupo de trabajo</span>
        <input id="cfg-workgroup" class="input-field input-field--mono" value="${escapeHtml(model.workgroup || '')}" />
      </label>
      <label class="field nx-span-2">
        <span class="nx-eyebrow">Descripción del servidor</span>
        <input id="cfg-server-string" class="input-field" value="${escapeHtml(model.server_string || '')}" />
      </label>
      <label class="field">
        <span class="nx-eyebrow">Acceso invitado</span>
        <select id="cfg-map-to-guest" class="input-field">
          <option value="Never" ${model.map_to_guest === 'Never' ? 'selected' : ''}>Never (recomendado)</option>
          <option value="Bad User" ${model.map_to_guest === 'Bad User' ? 'selected' : ''}>Bad User</option>
          <option value="Bad Password" ${model.map_to_guest === 'Bad Password' ? 'selected' : ''}>Bad Password</option>
        </select>
      </label>
    </div>
    <div class="nx-row-head">
      <span class="nx-eyebrow">Shares publicadas</span>
      <button type="button" class="btn btn-ghost btn-sm" id="cfg-add-share">Añadir share</button>
    </div>
    <div id="cfg-shares" class="nx-t-3 nx-stack nx-stack--tight">
      ${shares.map((share, index) => sambaShareRow(index, share)).join('')}
    </div>`;
}

function sambaShareRow(index, share) {
  return `
    <fieldset class="nx-subcard" data-share-index="${index}">
      <div class="nx-grid-3">
        <label class="field">
          <span class="nx-eyebrow">Nombre</span>
          <input class="input-field input-field--mono" data-field="name" value="${escapeHtml(share.name || '')}" />
        </label>
        <label class="field nx-span-2">
          <span class="nx-eyebrow">Ruta</span>
          <input class="input-field input-field--mono" data-field="path" value="${escapeHtml(share.path || '')}" />
        </label>
        <label class="field nx-span-3">
          <span class="nx-eyebrow">Comentario</span>
          <input class="input-field" data-field="comment" value="${escapeHtml(share.comment || '')}" />
        </label>
      </div>
      <div class="nx-checks">
        <label class="nx-check"><input type="checkbox" data-field="read_only" ${share.read_only ? 'checked' : ''} /><span>Solo lectura</span></label>
        <label class="nx-check"><input type="checkbox" data-field="browseable" ${share.browseable ? 'checked' : ''} /><span>Visible en red</span></label>
        <label class="nx-check"><input type="checkbox" data-field="guest_ok" ${share.guest_ok ? 'checked' : ''} /><span>Acceso invitado</span></label>
        <label class="nx-check"><span>Usuarios</span>
          <input class="input-field input-field--mono" data-field="valid_users" value="${escapeHtml(share.valid_users || '')}" placeholder="(todos)" /></label>
        <button type="button" class="btn btn-text btn-sm" data-remove-share="${index}">Quitar</button>
      </div>
    </fieldset>`;
}

function dnsmasqFormHtml(model) {
  const dhcp = model.dhcp || {};
  return `
    <label class="field">
      <span class="nx-eyebrow">Servidores DNS upstream · uno por línea</span>
      <textarea id="cfg-dns-servers" rows="3" class="input-field input-field--mono" placeholder="9.9.9.9">${escapeHtml((model.servers || []).join('\n'))}</textarea>
    </label>

    <div class="nx-row-head">
      <span class="nx-eyebrow">Registros locales · nombre e IP</span>
      <button type="button" class="btn btn-ghost btn-sm" id="cfg-add-record">Añadir registro</button>
    </div>
    <div id="cfg-records" class="nx-t-3 nx-stack nx-stack--dense">
      ${(model.records || []).map((record, index) => dnsRecordRow(index, record)).join('')}
    </div>

    <fieldset class="nx-subcard nx-t-4">
      <label class="nx-check nx-check--strong">
        <input type="checkbox" id="cfg-dhcp-enabled" ${dhcp.enabled ? 'checked' : ''} />
        <span>Activar el servidor DHCP de dnsmasq</span>
      </label>
      <div class="nx-t-4 nx-grid-3">
        <label class="field">
          <span class="nx-eyebrow">Inicio</span>
          <input id="cfg-dhcp-start" class="input-field input-field--mono" value="${escapeHtml(dhcp.start || '')}" />
        </label>
        <label class="field">
          <span class="nx-eyebrow">Fin</span>
          <input id="cfg-dhcp-end" class="input-field input-field--mono" value="${escapeHtml(dhcp.end || '')}" />
        </label>
        <label class="field">
          <span class="nx-eyebrow">Concesión</span>
          <input id="cfg-dhcp-lease" class="input-field input-field--mono" value="${escapeHtml(dhcp.lease_time || '12h')}" />
        </label>
      </div>
      <div class="nx-row-head">
        <span class="nx-eyebrow">Reservas por MAC</span>
        <button type="button" class="btn btn-ghost btn-sm" id="cfg-add-reservation">Añadir reserva</button>
      </div>
      <div id="cfg-reservations" class="nx-t-3 nx-stack nx-stack--dense">
        ${(model.reservations || []).map((reservation, index) => reservationRow(index, reservation)).join('')}
      </div>
      <button type="button" class="btn btn-ghost btn-sm nx-t-4" id="cfg-dhcp-check">Buscar otro DHCP en la red</button>
    </fieldset>`;
}

function dnsRecordRow(index, record) {
  return `
    <div class="nx-field-row" data-record-index="${index}">
      <input class="input-field input-field--mono" data-field="name" placeholder="nas.local" value="${escapeHtml(record.name || '')}" />
      <input class="input-field input-field--mono" data-field="address" placeholder="192.168.1.10" value="${escapeHtml(record.address || '')}" />
      <button type="button" class="btn btn-text btn-sm" data-remove-record="${index}">Quitar</button>
    </div>`;
}

function reservationRow(index, reservation) {
  return `
    <div class="nx-field-row nx-field-row--3" data-reservation-index="${index}">
      <input class="input-field input-field--mono" data-field="mac" placeholder="aa:bb:cc:dd:ee:ff" value="${escapeHtml(reservation.mac || '')}" />
      <input class="input-field input-field--mono" data-field="address" placeholder="192.168.1.20" value="${escapeHtml(reservation.address || '')}" />
      <input class="input-field input-field--mono" data-field="hostname" placeholder="nombre (opcional)" value="${escapeHtml(reservation.hostname || '')}" />
      <button type="button" class="btn btn-text btn-sm" data-remove-reservation="${index}">Quitar</button>
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
      box.innerHTML = '<p class="nx-hint">Todavía no hay copias de seguridad de este fichero.</p>';
      return;
    }
    box.innerHTML = '<p class="nx-eyebrow">Copias de seguridad · últimas 20</p>' +
      '<div class="nx-t-2 nx-field-list">' + data.backups.map((backup) => `
      <div class="nx-label-row">
        <span class="nx-kv-value nx-kv-value--plain nx-grow">${escapeHtml(backup.name)} · ${escapeHtml(backup.size)} B · ${escapeHtml(backup.modified)}</span>
        <button class="btn btn-ghost btn-sm" data-restore-backup="${escapeHtml(backup.name)}">Restaurar</button>
      </div>`).join('') + '</div>';
    box.querySelectorAll('[data-restore-backup]').forEach((button) => {
      button.addEventListener('click', () => restoreBackup(service, button.dataset.restoreBackup));
    });
  } catch (error) {
    box.innerHTML = `<p class="nx-status nx-status--error">No se pudieron leer las copias: ${escapeHtml(error.message)}</p>`;
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
      // Sin emoji: el nombre de la herramienta en mono es el dato.
      chip.className = 'action-chip';
      chip.textContent = action.tool;
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
    '<span class="nx-hint" id="typing-text">Pensando…</span> ' +
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
      '<div class="chat-bubble assistant">Conversación reiniciada. Dime qué necesitas del sistema.</div>';
  }
}

// ==================== Red: cambio de IP (Fase 6) ====================
let netcfgToken = null;
let netcfgTimer = null;

function setNetStatus(message, kind) {
  const box = $('netcfg-status');
  const suffix = kind === 'ok' ? ' nx-status--ok' : kind === 'error' ? ' nx-status--error' : '';
  box.textContent = message;
  box.className = `nx-status${suffix}`;
  box.classList.remove('hidden');
}

/** Traduce el inventario del agente al estado que pinta la tabla local. */
function adaptRemoteNetworkState(data) {
  const stats = (data && data.interfaces) || {};
  const addresses = (data && data.addresses) || {};
  const interfaces = Object.entries(stats).map(([name, item]) => {
    const raw = Array.isArray(addresses[name]) ? addresses[name] : [];
    const ips = raw.filter((value) => {
      const text = String(value);
      if (/^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$/i.test(text)) return false; // MAC
      return /^\d{1,3}(\.\d{1,3}){3}$/.test(text) || text.includes(':');
    });
    return {
      name,
      up: Boolean(item && item.up),
      addresses: ips,
      cidrs: [],
      netmasks: [],
      mac: null,
      mtu: item && item.mtu != null ? item.mtu : null
    };
  });
  return {
    available: true,
    interfaces,
    gateway: null,
    default_interface: null,
    renderer: 'agente',
    cloud_init: false,
    supported: false
  };
}

async function fetchNetworkState() {
  if (isRemoteScope()) {
    try {
      const adapted = adaptRemoteNetworkState(
        await hostCommand('network', { action: 'interfaces' })
      );
      const renderer = $('netcfg-renderer');
      if (renderer) renderer.textContent = 'Gestor: agente remoto';
      renderNetworkInterfaces(adapted);
      const box = $('netcfg-state');
      if (box) {
        box.textContent =
          'Inventario de interfaces del host remoto. La configuración de red solo se aplica en el host local.';
      }
    } catch (error) {
      const box = $('netcfg-state');
      if (box) box.textContent = 'Error leyendo las interfaces del agente: ' + error.message;
    }
    return;
  }
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
    tbody.innerHTML = emptyCell(6, 'Sin estado de red', 'Este sistema no expone el estado de sus interfaces.');
    return;
  }

  const interfaces = state.interfaces || [];
  if (summary) {
    summary.textContent =
      `${interfaces.length} interfaces · puerta de enlace ${state.gateway || '—'} · gestor ${state.renderer}` +
      (state.cloud_init ? ' · cloud-init detectado' : '');
  }

  if (interfaces.length === 0) {
    tbody.innerHTML = emptyCell(6, 'Sin interfaces', 'No se ha detectado ninguna interfaz de red.');
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
            <span class="nx-cell-id">${escapeHtml(iface.name)}</span>
            ${isDefault ? ' <span class="badge badge-running"><i class="badge-led" aria-hidden="true"></i>predeterminada</span>' : ''}
          </td>
          <td>${badgeHtml(iface.up ? 'badge-running' : 'badge-stopped', iface.up ? 'Activa' : 'Inactiva')}</td>
          <td class="nx-cell-num">${escapeHtml(addresses.join(', ') || 'sin IP')}</td>
          <td class="nx-cell-num nx-cell-quiet">${escapeHtml(masks.join(', ') || '—')}</td>
          <td class="nx-cell-num nx-cell-quiet">${escapeHtml(iface.mac || '—')}</td>
          <td class="nx-cell-num nx-cell-quiet">${iface.mtu == null ? '—' : escapeHtml(String(iface.mtu))}</td>
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
  if (isRemoteScope()) {
    showToast('La configuración de red solo se aplica en el host local.', 'error');
    return;
  }
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
  if (isRemoteScope()) {
    showToast('La configuración de red solo se aplica en el host local.', 'error');
    return;
  }
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
      ? `Cambio aplicado. Abre el panel en la nueva dirección (http://${newAddress}:8000) y confirma en ${remaining} s; si no, la red se revierte sola.`
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
      <p class="nx-eyebrow">Propuesta pendiente de confirmación</p>
      <p class="nx-t-2">${escapeHtml(action.summary)}</p>
      <p class="nx-t-2 nx-hint">Caduca ${escapeHtml(action.expires_at)}</p>
      <div class="nx-t-3 nx-hstack">
        <button class="btn btn-primary btn-sm" data-action-confirm="${escapeHtml(action.id)}">Confirmar y ejecutar</button>
        <button class="btn btn-ghost btn-sm" data-action-cancel="${escapeHtml(action.id)}">Cancelar</button>
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

// ==========================================================
// Alertas
// ------------------------------------------------------------
// Cinco bloques: reglas, eventos, canales, ventanas de
// mantenimiento y propuestas de remediación. Cada uno se carga
// la primera vez que se abre su sub-pestaña, y no antes.
// ==========================================================

/** Severidad de cada regla, tomada del ÚLTIMO evento que abrió.
 *  El backend no guarda una severidad en la regla —la calcula al
 *  disparar—, así que la columna no puede inventarla: muestra lo que
 *  pasó la última vez y "sin eventos" cuando la regla nunca disparó.
 */
function severityByRule() {
  const worst = Object.create(null);
  // Los eventos llegan del más nuevo al más viejo (id DESC), así que
  // la primera vez que se ve una regla es su evento más reciente.
  state.alertEvents.forEach((event) => {
    if (!event.rule_id) return;
    if (worst[event.rule_id] !== undefined) return;
    worst[event.rule_id] = event.severity === 'crit' ? 'crit'
      : event.severity === 'warn' ? 'warn' : null;
  });
  return worst;
}

/** "> 91 % · recupera en 85 %" con el operador apagado. La métrica
 *  NO se repite aquí: ya tiene su propia columna, y escribirla dos
 *  veces en la misma fila convierte la lectura en un escaneo. Es la
 *  forma en que PRTG y Zabbix parten la fila: objeto, predicado,
 *  duración. */
function ruleConditionHtml(rule) {
  const meta = METRIC_META[rule.metric];
  const unit = meta ? meta.unit : '';
  const threshold = trimNumber(rule.threshold, 2);
  const clear = rule.clear_threshold === null || rule.clear_threshold === undefined
    ? ''
    : ` · recupera en ${trimNumber(rule.clear_threshold, 2)}${unit ? ` ${unit}` : ''}`;
  return `<span class="nx-cond"><span class="nx-cond-op">${escapeHtml(rule.op)}</span> ` +
    `${escapeHtml(threshold)}${unit ? ` ${escapeHtml(unit)}` : ''}${escapeHtml(clear)}</span>`;
}

function renderAlertRules() {
  const tbody = $('alert-rules-table');
  if (!tbody) return;
  const severity = severityByRule();

  if (state.alertRules.length === 0) {
    tbody.innerHTML = emptyCell(8, 'Sin reglas', 'No hay ninguna regla de alarma definida. Crea la primera con "Nueva regla".');
    return;
  }

  tbody.innerHTML = state.alertRules
    .map((rule) => {
      const sev = severity[rule.id];
      const remediation = rule.remediation_action
        ? `<span class="nx-cond">${escapeHtml(rule.remediation_action)}</span>`
        : '<span class="nx-cell-quiet">Ninguna</span>';
      return `
        <tr>
          <td class="nx-cell-strong">${escapeHtml(rule.name)}</td>
          <td class="nx-cond">${escapeHtml(rule.metric)}</td>
          <td>${ruleConditionHtml(rule)}</td>
          <td class="nx-cell-num nx-cell-quiet">${escapeHtml(formatSecondsShort(rule.duration_s))}</td>
          <td>${sev ? alertSeverityBadge(sev) : '<span class="nx-cell-quiet">Sin eventos</span>'}</td>
          <td>${remediation}</td>
          <td>${Number(rule.enabled) ? badgeHtml('badge-running', 'Activa') : badgeHtml('badge-stopped', 'Inactiva')}</td>
          <td class="nx-cell-actions">
            <span class="nx-btn-row">
              <button class="btn btn-ghost btn-sm" data-rule-edit="${escapeHtml(String(rule.id))}">Editar</button>
              <button class="btn btn-danger btn-sm" data-rule-delete="${escapeHtml(String(rule.id))}" data-rule-name="${escapeHtml(rule.name)}">Eliminar</button>
            </span>
          </td>
        </tr>`;
    })
    .join('');

  tbody.querySelectorAll('[data-rule-edit]').forEach((button) => {
    button.addEventListener('click', () => openRuleForm(Number(button.dataset.ruleEdit)));
  });
  tbody.querySelectorAll('[data-rule-delete]').forEach((button) => {
    button.addEventListener('click', () => deleteAlertRule(Number(button.dataset.ruleDelete), button.dataset.ruleName));
  });
}

/** Abre el formulario en modo alta o edición. El alta deja los
 *  campos vacíos y pone el foco en el nombre; la edición rellena
 *  desde la regla y cambia la nota del pie para que se sepa cuál
 *  de las dos cosas está pasando. */
function openRuleForm(ruleId) {
  const wrap = $('alert-rule-form-wrap');
  const form = $('alert-rule-form');
  if (!wrap || !form) return;

  const rule = ruleId ? state.alertRules.find((item) => item.id === ruleId) : null;
  state.editingRuleId = rule ? rule.id : null;

  form.reset();
  $('rule-enabled').checked = true;
  $('rule-duration').value = '0';
  $('rule-remediation').value = '';

  if (rule) {
    $('rule-name').value = rule.name;
    $('rule-metric').value = rule.metric;
    $('rule-op').value = rule.op;
    $('rule-threshold').value = trimNumber(rule.threshold, 4);
    $('rule-clear-threshold').value = rule.clear_threshold === null || rule.clear_threshold === undefined
      ? '' : trimNumber(rule.clear_threshold, 4);
    $('rule-duration').value = String(rule.duration_s);
    $('rule-remediation').value = rule.remediation_action || '';
    $('rule-enabled').checked = Number(rule.enabled) === 1;
    $('rule-form-note').textContent = `Editando la regla ${rule.id}`;
  } else {
    $('rule-form-note').textContent = 'Se creará una regla nueva';
  }

  $('rule-status').classList.add('hidden');
  wrap.classList.remove('hidden');
  $('rule-name').focus();
}

function closeRuleForm() {
  state.editingRuleId = null;
  const wrap = $('alert-rule-form-wrap');
  if (wrap) wrap.classList.add('hidden');
}

/** Valida el par umbral/recuperación con la misma regla que el
 *  servidor: con ">" el valor de recuperación tiene que estar POR
 *  DEBAJO, y con "<" POR ENCIMA. Un par imposible no se envía: es
 *  mejor un mensaje aquí que un 422 con el campo marcado. */
function validateRuleThresholds(op, threshold, clearThreshold) {
  if (clearThreshold === null || clearThreshold === undefined) return null;
  if (op === '>' && clearThreshold >= threshold) {
    return 'Con "mayor que", el umbral de recuperación debe estar por debajo del umbral de alarma.';
  }
  if (op === '<' && clearThreshold <= threshold) {
    return 'Con "menor que", el umbral de recuperación debe estar por encima del umbral de alarma.';
  }
  return null;
}

function collectRulePayload() {
  const clearRaw = $('rule-clear-threshold').value.trim();
  return {
    name: $('rule-name').value.trim(),
    metric: $('rule-metric').value,
    // El campo del backend se llama `op`. Escribirlo como
    // "operator" devolvía un 422 con "falta op" y nada más.
    op: $('rule-op').value,
    threshold: Number($('rule-threshold').value),
    clear_threshold: clearRaw === '' ? null : Number(clearRaw),
    duration_s: Number($('rule-duration').value || 0),
    enabled: $('rule-enabled').checked,
    // El servidor no admite la cadena "none": "sin remediación" es
    // la ausencia del campo, que es como lo modela el backend.
    remediation_action: $('rule-remediation').value || null
  };
}

async function submitRule(event) {
  event.preventDefault();
  const payload = collectRulePayload();

  if (!payload.name) {
    setFormStatus('rule-status', 'Ponle un nombre a la regla.', 'warn');
    return;
  }
  if (!Number.isFinite(payload.threshold)) {
    setFormStatus('rule-status', 'El umbral de alarma tiene que ser un número.', 'warn');
    return;
  }
  const thresholdError = validateRuleThresholds(payload.op, payload.threshold, payload.clear_threshold);
  if (thresholdError) {
    setFormStatus('rule-status', thresholdError, 'warn');
    return;
  }

  const editing = state.editingRuleId;
  try {
    if (editing) {
      await mutate(`/api/alert-rules/${editing}`, 'PATCH', payload, `Regla "${payload.name}" guardada.`);
    } else {
      await mutate('/api/alert-rules', 'POST', payload, `Regla "${payload.name}" creada.`);
    }
    closeRuleForm();
    await Promise.all([fetchAlertRules(), ensureAlertEvents()]);
    renderAlertRules();
  } catch (error) {
    setFormStatus('rule-status', `No se pudo guardar: ${error.message}`, 'error');
  }
}

async function deleteAlertRule(ruleId, name) {
  if (!window.confirm(`¿Eliminar la regla "${name}"? Los eventos que abrió se cerrarán.`)) return;
  try {
    const result = await mutate(`/api/alert-rules/${ruleId}`, 'DELETE', null);
    showToast(result.message || 'Regla eliminada.', 'success');
    if (state.editingRuleId === ruleId) closeRuleForm();
    await Promise.all([fetchAlertRules(), ensureAlertEvents()]);
    renderAlertRules();
  } catch (error) {
    showToast(`No se pudo eliminar: ${error.message}`, 'error');
  }
}

async function fetchAlertRules() {
  const tbody = $('alert-rules-table');
  if (!tbody) return;
  try {
    const data = await apiFetch('/api/alert-rules');
    state.alertRules = data.rules || [];
    renderAlertRules();
  } catch (error) {
    state.alertRules = [];
    tbody.innerHTML = emptyCell(8, 'No se pudieron leer las reglas', error.message, true);
  }
}

// ------------------- Eventos -------------------
function renderAlertEvents() {
  const tbody = $('alert-events-table');
  if (!tbody) return;

  // Los contadores del riel salen de la lista COMPLETA, no de la
  // filtrada: si no, al pulsar "Cerrados" el resto de contadores se
  // ponen a cero y el filtro deja de explicar nada.
  const counts = { open: 0, acked: 0, closed: 0 };
  state.alertEvents.forEach((event) => {
    const key = String(event.status || '').toLowerCase();
    if (counts[key] !== undefined) counts[key] += 1;
  });
  document.querySelectorAll('[data-event-count]').forEach((node) => {
    const total = counts[node.dataset.eventCount];
    node.textContent = total > 0 ? ` ${total}` : '';
  });

  const filtered = state.eventFilter === 'all'
    ? state.alertEvents
    : state.alertEvents.filter((event) => String(event.status || '').toLowerCase() === state.eventFilter);

  const badge = $('alerts-events-count');
  if (badge) {
    const open = counts.open;
    badge.textContent = open > 0 ? ` ${open}` : '';
  }

  if (filtered.length === 0) {
    const label = state.eventFilter === 'all' ? 'Sin eventos' : 'Sin eventos con ese estado';
    const text = state.eventFilter === 'all'
      ? 'Ninguna regla ha disparado todavía.'
      : 'Prueba con el filtro "Todos" para ver el resto del registro.';
    tbody.innerHTML = emptyCell(8, label, text);
    return;
  }

  tbody.innerHTML = filtered
    .map((event) => {
      const status = String(event.status || '').toLowerCase();
      const isOpen = status === 'open';
      // El backend solo aplica el aplazamiento a eventos que no
      // estén cerrados, así que "reconocer" y "aplazar" se pueden
      // encadenar. Reconocer, en cambio, solo hace algo sobre un
      // evento abierto: en los otros es un no-op y el botón se
      // apaga en vez de fingir que ha pasado algo.
      const canSnooze = status !== 'closed';
      const ackedBy = event.acked_by
        ? `${escapeHtml(event.acked_by)} · ${escapeHtml(formatStamp(event.acked_at))}`
        : '—';
      const snooze = event.snooze_until
        ? `<p class="nx-hint nx-t-2">Aplazado hasta ${escapeHtml(formatStamp(event.snooze_until))}</p>`
        : '';
      return `
        <tr>
          <td><i class="nx-led ${event.severity === 'crit' ? LED_CLASS.crit : LED_CLASS.warn}" aria-hidden="true"></i></td>
          <td class="nx-cell-strong">${escapeHtml(event.message || `${event.metric} sin mensaje`)}${snooze}</td>
          <td class="nx-cell-num">${escapeHtml(trimNumber(event.value, 2))}</td>
          <td class="nx-cell-num nx-cell-quiet">${escapeHtml(trimNumber(event.threshold, 2))}</td>
          <td class="nx-cell-quiet">${escapeHtml(formatStamp(event.opened_at))}</td>
          <td>${alertStatusBadge(event.status)}</td>
          <td class="nx-cell-quiet">${ackedBy}</td>
          <td class="nx-cell-actions">
            <span class="nx-btn-row">
              <button class="btn btn-ghost btn-sm" data-event-ack="${escapeHtml(String(event.id))}"${isOpen ? '' : ' disabled'}>Reconocer</button>
              <button class="btn btn-ghost btn-sm" data-event-snooze="${escapeHtml(String(event.id))}"${canSnooze ? '' : ' disabled'}>Aplazar 15 min</button>
            </span>
          </td>
        </tr>`;
    })
    .join('');

  tbody.querySelectorAll('[data-event-ack]').forEach((button) => {
    button.addEventListener('click', () => ackAlertEvent(Number(button.dataset.eventAck)));
  });
  tbody.querySelectorAll('[data-event-snooze]').forEach((button) => {
    button.addEventListener('click', () => snoozeAlertEvent(Number(button.dataset.eventSnooze)));
  });
}

/** Carga los eventos una vez para que la tabla de reglas pueda derivar
 *  la severidad aunque el usuario nunca abra la sub-pestaña Eventos. */
let alertEventsLoaded = false;
async function ensureAlertEvents() {
  if (alertEventsLoaded) return;
  await fetchAlertEvents();
  alertEventsLoaded = true;
}

async function fetchAlertEvents() {
  const tbody = $('alert-events-table');
  if (!tbody) return;
  try {
    // El filtro va en el servidor: `status` es un patrón cerrado
    // (open|acked|closed) y filtrar en el navegador traería la
    // página recortada de eventos que no son los que se piden.
    const query = state.eventFilter === 'all' ? '?limit=200' : `?status=${state.eventFilter}&limit=200`;
    const data = await apiFetch(`/api/alert-events${query}`);
    state.alertEvents = data.events || [];
    renderAlertEvents();
    // La columna de severidad de la tabla de reglas se deriva de
    // estos mismos eventos, así que hay que repintarla también: si
    // no, la primera carga dejaría todas las reglas en "Sin eventos"
    // hasta que se volviera a pedir la lista de reglas.
    renderAlertRules();
  } catch (error) {
    state.alertEvents = [];
    tbody.innerHTML = emptyCell(8, 'No se pudieron leer los eventos', error.message, true);
  }
}

async function ackAlertEvent(eventId) {
  try {
    await mutate(`/api/alert-events/${eventId}/ack`, 'POST', {}, `Evento ${eventId} reconocido.`);
    await fetchAlertEvents();
  } catch (error) {
    showToast(`No se pudo reconocer: ${error.message}`, 'error');
  }
}

async function snoozeAlertEvent(eventId) {
  try {
    await mutate(
      `/api/alert-events/${eventId}/snooze`,
      'POST',
      { minutes: 15 },
      `Evento ${eventId} aplazado 15 minutos.`
    );
    await fetchAlertEvents();
  } catch (error) {
    showToast(`No se pudo aplazar: ${error.message}`, 'error');
  }
}

// ------------------- Canales -------------------
/** Destino legible de un canal según su tipo. Los secretos llegan
 *  enmascarados del servidor ("••••••-DEF") y se muestran tal cual:
 *  el panel no tiene nada más que enseñar y fingir un destino limpio
 *  sería peor que repetir la máscara. */
function channelTargetHtml(channel) {
  const config = channel.config || {};
  if (channel.kind === 'telegram') {
    return config.chat_id
      ? `<span class="nx-cond">chat ${escapeHtml(config.chat_id)}</span>`
      : '<span class="nx-cell-quiet">—</span>';
  }
  if (channel.kind === 'webhook') {
    return config.url
      ? `<span class="nx-cond">${escapeHtml(config.url)}</span>`
      : '<span class="nx-cell-quiet">—</span>';
  }
  if (channel.kind === 'smtp') {
    const pieces = [config.host, config.to].filter(Boolean).map(escapeHtml);
    return pieces.length ? `<span class="nx-cond">${pieces.join(' → ')}</span>` : '<span class="nx-cell-quiet">—</span>';
  }
  return '<span class="nx-cell-quiet">—</span>';
}

function renderChannels() {
  const tbody = $('channels-table');
  if (!tbody) return;

  if (state.channels.length === 0) {
    tbody.innerHTML = emptyCell(6, 'Sin canales', 'Sin canales de notificación: las alertas solo se verán aquí.');
    return;
  }

  tbody.innerHTML = state.channels
    .map((channel) => {
      const last = channel.last_ok_at
        ? escapeHtml(formatStamp(channel.last_ok_at))
        : '<span class="nx-cell-quiet">Nunca</span>';
      const failed = channel.last_error
        ? `<p class="nx-hint nx-t-2">${escapeHtml(channel.last_error)}</p>`
        : '';
      return `
        <tr>
          <td class="nx-cell-strong">${escapeHtml(channel.name)}</td>
          <td class="nx-cond">${escapeHtml(channel.kind)}</td>
          <td>${channelTargetHtml(channel)}${failed}</td>
          <td>${Number(channel.enabled) ? badgeHtml('badge-running', 'Activo') : badgeHtml('badge-stopped', 'Inactivo')}</td>
          <td class="nx-cell-quiet">${last}</td>
          <td class="nx-cell-actions">
            <span class="nx-btn-row">
              <button class="btn btn-ghost btn-sm" data-channel-test="${escapeHtml(String(channel.id))}">Probar</button>
              <button class="btn btn-ghost btn-sm" data-channel-edit="${escapeHtml(String(channel.id))}">Editar</button>
              <button class="btn btn-danger btn-sm" data-channel-delete="${escapeHtml(String(channel.id))}" data-channel-name="${escapeHtml(channel.name)}">Eliminar</button>
            </span>
          </td>
        </tr>`;
    })
    .join('');

  tbody.querySelectorAll('[data-channel-test]').forEach((button) => {
    button.addEventListener('click', () => testChannel(Number(button.dataset.channelTest)));
  });
  tbody.querySelectorAll('[data-channel-edit]').forEach((button) => {
    button.addEventListener('click', () => openChannelForm(Number(button.dataset.channelEdit)));
  });
  tbody.querySelectorAll('[data-channel-delete]').forEach((button) => {
    button.addEventListener('click', () => deleteChannel(Number(button.dataset.channelDelete), button.dataset.channelName));
  });
}

/** Muestra u oculta los campos que el tipo elegido exige. El servidor
 *  valida por tipo ("Telegram necesita 'token' y 'chat_id'"), así que
 *  el formulario tiene que enseñar exactamente los que se van a usar. */
function applyChannelKindFields() {
  const kind = $('channel-kind').value;
  const map = {
    telegram: 'channel-fields-telegram',
    webhook: 'channel-fields-webhook',
    smtp: 'channel-fields-smtp'
  };
  Object.keys(map).forEach((key) => {
    const node = $(map[key]);
    if (node) node.classList.toggle('hidden', key !== kind);
  });
}

function openChannelForm(channelId) {
  const wrap = $('channel-form-wrap');
  const form = $('channel-form');
  if (!wrap || !form) return;

  const channel = channelId ? state.channels.find((item) => item.id === channelId) : null;
  state.editingChannelId = channel ? channel.id : null;

  form.reset();
  $('channel-enabled').value = '1';

  if (channel) {
    const config = channel.config || {};
    $('channel-name').value = channel.name;
    $('channel-kind').value = channel.kind;
    $('channel-enabled').value = Number(channel.enabled) ? '1' : '0';
    $('channel-tg-chat').value = config.chat_id || '';
    $('channel-wh-url').value = config.url || '';
    $('channel-smtp-host').value = config.host || '';
    $('channel-smtp-port').value = config.port || '587';
    $('channel-smtp-user').value = config.user || '';
    $('channel-smtp-from').value = config.from_addr || '';
    $('channel-smtp-to').value = config.to || '';
    // Las credenciales NUNCA se rellenan: el servidor solo devuelve
    // una máscara y el panel no tiene el valor real.
    $('channel-tg-token').value = '';
    $('channel-wh-auth').value = '';
    $('channel-smtp-pass').value = '';
    $('channel-form-note').textContent = `Editando el canal ${channel.id}`;
  } else {
    $('channel-form-note').textContent = 'Se creará un canal nuevo';
  }

  applyChannelKindFields();
  $('channel-status').classList.add('hidden');
  wrap.classList.remove('hidden');
  $('channel-name').focus();
}

function closeChannelForm() {
  state.editingChannelId = null;
  const wrap = $('channel-form-wrap');
  if (wrap) wrap.classList.add('hidden');
}

/** Claves de configuración NO secretas por tipo: son las que el
 *  servidor devuelve en claro y las únicas que el formulario puede
 *  comparar con lo guardado. El resto (token, authorization,
 *  password) llega enmascarado, así que no sirve para decidir nada. */
const CHANNEL_FORM_KEYS = {
  telegram: ['chat_id'],
  webhook: ['url'],
  smtp: ['host', 'port', 'user', 'from_addr', 'to']
};
/** Campo de entrada ↔ clave de configuración, para poder leer del
 *  formulario en la misma clave que usa el backend. */
const CHANNEL_INPUT_OF_KEY = {
  chat_id: 'channel-tg-chat',
  url: 'channel-wh-url',
  host: 'channel-smtp-host',
  port: 'channel-smtp-port',
  user: 'channel-smtp-user',
  from_addr: 'channel-smtp-from',
  to: 'channel-smtp-to'
};
/** El único campo de credencial de cada tipo. */
function channelSecretInput(kind) {
  if (kind === 'telegram') return 'channel-tg-token';
  if (kind === 'webhook') return 'channel-wh-auth';
  return 'channel-smtp-pass';
}

/** Monta la configuración del canal desde el formulario. Devuelve
 *  `null` si falta algún campo OBLIGATORIO (los no secretos). */
function collectChannelConfig() {
  const kind = $('channel-kind').value;
  const config = {};
  (CHANNEL_FORM_KEYS[kind] || []).forEach((key) => {
    const value = $(CHANNEL_INPUT_OF_KEY[key]).value.trim();
    if (value !== '') config[key] = key === 'port' ? Number(value) : value;
  });
  // Obligatorios por tipo: sin ellos el backend responde 400 con un
  // motivo propio, pero es mejor no provocarlo.
  const required = { telegram: ['chat_id'], webhook: ['url'], smtp: ['host', 'from_addr', 'to'] }[kind] || [];
  if (required.some((key) => !config[key])) return null;

  const secret = $(channelSecretInput(kind)).value.trim();
  if (secret) config.token_or_password = secret;
  return config;
}

/** ¿El usuario ha escrito alguna credencial? Decide si el PATCH
 *  puede llevar `config` entero —y con ello una credencial real— o
 *  si tiene que omitirlo para no pisar la que ya está guardada. */
function channelSecretTyped(kind) {
  return $(channelSecretInput(kind)).value.trim() !== '';
}

/** ¿Han cambiado los campos que el formulario SÍ conoce? Solo se
 *  comparan esos: el canal guardado puede traer claves que aquí no
 *  se editan (cabeceras del webhook, por ejemplo) y contarlas como
 *  cambio daría un aviso falso. */
function channelTargetChanged(kind, current, config) {
  return (CHANNEL_FORM_KEYS[kind] || []).some((key) => {
    const before = current[key] === undefined || current[key] === null ? '' : String(current[key]);
    const after = config[key] === undefined ? '' : String(config[key]);
    return before !== after;
  });
}

/** Traduce el objeto de configuración al nombre real de la clave
 *  secreta de cada tipo, que no es el mismo en los tres. */
function channelConfigWithSecret(kind, config) {
  const result = { ...config };
  delete result.token_or_password;
  const secret = $(channelSecretInput(kind)).value.trim();
  if (kind === 'telegram') result.token = secret;
  else if (kind === 'webhook') result.authorization = secret;
  else result.password = secret;
  return result;
}

async function submitChannel(event) {
  event.preventDefault();
  const name = $('channel-name').value.trim();
  const kind = $('channel-kind').value;
  const editing = state.editingChannelId;
  const config = collectChannelConfig();

  if (!name) {
    setFormStatus('channel-status', 'Ponle un nombre al canal.', 'warn');
    return;
  }
  if (config === null) {
    setFormStatus('channel-status', 'Rellena los campos obligatorios del tipo elegido.', 'warn');
    return;
  }

  try {
    if (!editing) {
      await mutate('/api/notification-channels', 'POST', {
        name,
        kind,
        config: channelConfigWithSecret(kind, config),
        enabled: $('channel-enabled').value === '1'
      }, `Canal "${name}" creado.`);
    } else if (channelSecretTyped(kind)) {
      // Credencial nueva: hay que mandar la configuración entera,
      // porque el PATCH reemplaza `config` en vez de fusionarlo.
      await mutate(`/api/notification-channels/${editing}`, 'PATCH', {
        name,
        kind,
        config: channelConfigWithSecret(kind, config),
        enabled: $('channel-enabled').value === '1'
      }, `Canal "${name}" guardado.`);
    } else {
      // Sin credencial escrita no se manda `config`: es la única
      // forma de renombrar un canal sin convertir su token en la
      // máscara. Si además ha cambiado el destino, no se puede
      // construir la configuración completa, así que se dice en vez
      // de enviar algo que rompería el canal.
      const channel = state.channels.find((item) => item.id === editing);
      const current = (channel && channel.config) || {};
      if (channelTargetChanged(kind, current, config)) {
        setFormStatus(
          'channel-status',
          'Para cambiar el destino hay que volver a escribir la credencial: al guardar, el canal recibe la configuración completa.',
          'warn'
        );
        return;
      }
      await mutate(`/api/notification-channels/${editing}`, 'PATCH', {
        name,
        kind,
        enabled: $('channel-enabled').value === '1'
      }, `Canal "${name}" guardado.`);
    }
    closeChannelForm();
    await fetchChannels();
  } catch (error) {
    setFormStatus('channel-status', `No se pudo guardar: ${error.message}`, 'error');
  }
}

async function testChannel(channelId) {
  const channel = state.channels.find((item) => item.id === channelId);
  const label = channel ? channel.name : `canal ${channelId}`;
  // El backend responde 200 con ok=false cuando el destino está mal
  // o no contesta: por eso se lee el campo, no el código HTTP.
  try {
    const result = await apiFetch(`/api/notification-channels/${channelId}/test`, { method: 'POST', body: '{}' });
    if (result.ok) {
      showToast(`Prueba enviada por "${label}": el destino la aceptó.`, 'success');
    } else {
      showToast(`"${label}" no aceptó la prueba: ${result.detail}`, 'error');
    }
    await fetchChannels();
  } catch (error) {
    showToast(`No se pudo probar "${label}": ${error.message}`, 'error');
  }
}

async function deleteChannel(channelId, name) {
  if (!window.confirm(`¿Eliminar el canal "${name}"?`)) return;
  try {
    const result = await mutate(`/api/notification-channels/${channelId}`, 'DELETE', null);
    showToast(result.message || 'Canal eliminado.', 'success');
    if (state.editingChannelId === channelId) closeChannelForm();
    await fetchChannels();
  } catch (error) {
    showToast(`No se pudo eliminar: ${error.message}`, 'error');
  }
}

async function fetchChannels() {
  const tbody = $('channels-table');
  if (!tbody) return;
  try {
    const data = await apiFetch('/api/notification-channels');
    state.channels = data.channels || [];
    renderChannels();
  } catch (error) {
    state.channels = [];
    tbody.innerHTML = emptyCell(6, 'No se pudieron leer los canales', error.message, true);
  }
}

// ------------------- Ventanas de mantenimiento -------------------
function windowStateHtml(win) {
  if (!Number(win.enabled)) return badgeHtml('badge-stopped', 'Inactiva');
  const now = Date.now();
  const start = new Date(win.starts_at).getTime();
  const end = new Date(win.ends_at).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end)) return badgeHtml('badge-warn', 'Fechas inválidas');
  if (now >= start && now <= end) return badgeHtml('badge-running', 'En curso');
  return badgeHtml('badge-warn', 'Programada');
}

function renderWindows() {
  const tbody = $('mw-table');
  if (!tbody) return;

  if (state.windows.length === 0) {
    tbody.innerHTML = emptyCell(5, 'Sin ventanas', 'No hay ventanas de mantenimiento definidas: las reglas de alarma se evalúan siempre.');
    return;
  }

  tbody.innerHTML = state.windows
    .map((win) => `
      <tr>
        <td class="nx-cell-strong">${escapeHtml(win.name)}</td>
        <td class="nx-cell-quiet">${escapeHtml(formatStamp(win.starts_at))}</td>
        <td class="nx-cell-quiet">${escapeHtml(formatStamp(win.ends_at))}</td>
        <td>${windowStateHtml(win)}</td>
        <td class="nx-cell-actions">
          <span class="nx-btn-row">
            <button class="btn btn-ghost btn-sm" data-mw-edit="${escapeHtml(String(win.id))}">Editar</button>
            <button class="btn btn-danger btn-sm" data-mw-delete="${escapeHtml(String(win.id))}" data-mw-name="${escapeHtml(win.name)}">Eliminar</button>
          </span>
        </td>
      </tr>`)
    .join('');

  tbody.querySelectorAll('[data-mw-edit]').forEach((button) => {
    button.addEventListener('click', () => openWindowForm(Number(button.dataset.mwEdit)));
  });
  tbody.querySelectorAll('[data-mw-delete]').forEach((button) => {
    button.addEventListener('click', () => deleteWindow(Number(button.dataset.mwDelete), button.dataset.mwName));
  });
}

function openWindowForm(windowId) {
  const wrap = $('mw-form-wrap');
  const form = $('mw-form');
  if (!wrap || !form) return;

  const win = windowId ? state.windows.find((item) => item.id === windowId) : null;
  state.editingWindowId = win ? win.id : null;

  form.reset();
  $('mw-enabled').value = '1';

  if (win) {
    $('mw-name').value = win.name;
    $('mw-start').value = win.starts_at;
    $('mw-end').value = win.ends_at;
    $('mw-enabled').value = Number(win.enabled) ? '1' : '0';
    $('mw-form-note').textContent = `Editando la ventana ${win.id}`;
  } else {
    $('mw-form-note').textContent = 'Se creará una ventana nueva';
  }

  $('mw-status').classList.add('hidden');
  wrap.classList.remove('hidden');
  $('mw-name').focus();
}

function closeWindowForm() {
  state.editingWindowId = null;
  const wrap = $('mw-form-wrap');
  if (wrap) wrap.classList.add('hidden');
}

async function submitWindow(event) {
  event.preventDefault();
  const payload = {
    name: $('mw-name').value.trim(),
    starts_at: $('mw-start').value.trim(),
    ends_at: $('mw-end').value.trim(),
    enabled: $('mw-enabled').value === '1'
  };
  if (!payload.name) {
    setFormStatus('mw-status', 'Ponle un nombre a la ventana.', 'warn');
    return;
  }
  const start = new Date(payload.starts_at).getTime();
  const end = new Date(payload.ends_at).getTime();
  if (!payload.starts_at || !payload.ends_at) {
    setFormStatus('mw-status', 'Indica las dos fechas en ISO-8601, por ejemplo 2026-10-18T02:00:00Z.', 'warn');
    return;
  }
  if (Number.isNaN(start) || Number.isNaN(end)) {
    setFormStatus('mw-status', 'Alguna fecha no es válida. Usa el formato ISO-8601.', 'warn');
    return;
  }
  if (end <= start) {
    setFormStatus('mw-status', 'La ventana tiene que terminar después de empezar.', 'warn');
    return;
  }

  const editing = state.editingWindowId;
  try {
    if (editing) {
      await mutate(`/api/maintenance-windows/${editing}`, 'PATCH', payload, `Ventana "${payload.name}" guardada.`);
    } else {
      await mutate('/api/maintenance-windows', 'POST', payload, `Ventana "${payload.name}" creada.`);
    }
    closeWindowForm();
    await fetchWindows();
  } catch (error) {
    setFormStatus('mw-status', `No se pudo guardar: ${error.message}`, 'error');
  }
}

async function deleteWindow(windowId, name) {
  if (!window.confirm(`¿Eliminar la ventana "${name}"?`)) return;
  try {
    const result = await mutate(`/api/maintenance-windows/${windowId}`, 'DELETE', null);
    showToast(result.message || 'Ventana eliminada.', 'success');
    if (state.editingWindowId === windowId) closeWindowForm();
    await fetchWindows();
  } catch (error) {
    showToast(`No se pudo eliminar: ${error.message}`, 'error');
  }
}

async function fetchWindows() {
  const tbody = $('mw-table');
  if (!tbody) return;
  try {
    const data = await apiFetch('/api/maintenance-windows');
    state.windows = data.windows || [];
    renderWindows();
  } catch (error) {
    state.windows = [];
    tbody.innerHTML = emptyCell(5, 'No se pudieron leer las ventanas', error.message, true);
  }
}

// ------------------- Propuestas de remediación -------------------
function renderProposals() {
  const box = $('proposals-list');
  if (!box) return;

  const badge = $('alerts-proposals-count');
  if (badge) badge.textContent = state.proposals.length > 0 ? ` ${state.proposals.length}` : '';

  if (state.proposals.length === 0) {
    box.innerHTML = emptyBlock('Nada pendiente de tu decisión', 'No hay propuestas esperando confirmación. Las que se agoten solas desaparecen de esta lista.');
    return;
  }

  box.innerHTML = state.proposals
    .map((proposal) => {
      const args = proposal.args || {};
      // Los argumentos se enseñan tal cual los guarda el motor: son
      // exactamente lo que se ejecutaría al confirmar, y esconderlos
      // detrás de una frase sería pedirle fe al panel.
      const detail = Object.keys(args).length
        ? Object.entries(args)
          .filter(([key]) => key !== 'regla' && key !== 'evento')
          .map(([key, value]) => `${key}=${typeof value === 'object' ? JSON.stringify(value) : String(value)}`)
          .join(' · ')
        : 'sin argumentos';
      const expiresIn = new Date(proposal.expires_at).getTime() - Date.now();
      const minutes = Math.floor(expiresIn / 60000);
      const expiry = Number.isFinite(expiresIn)
        ? (minutes > 0 ? `caduca en ${minutes} min` : 'caduca en menos de un minuto')
        : `caducó el ${formatStamp(proposal.expires_at)}`;
      return `
        <div class="nx-device" data-proposal="${escapeHtml(proposal.id)}">
          <div class="nx-device-head">
            <span class="nx-device-name">${escapeHtml(proposal.action)}</span>
            ${badgeHtml('badge-warn', 'Propuesta')}
            <span class="nx-device-model">regla ${escapeHtml(proposal.rule_name || proposal.rule_id)}</span>
          </div>
          <p class="nx-hint nx-t-2">${escapeHtml(proposal.summary)}</p>
          <p class="nx-cond nx-t-2">${escapeHtml(detail || 'sin argumentos')}</p>
          <p class="nx-hint nx-t-2">${escapeHtml(expiry)} · evento ${escapeHtml(String(proposal.event_id ?? '—'))}</p>
          <div class="nx-actions nx-t-3">
            <button class="btn btn-primary btn-sm" data-proposal-confirm="${escapeHtml(proposal.id)}">Confirmar y ejecutar</button>
            <button class="btn btn-ghost btn-sm" data-proposal-cancel="${escapeHtml(proposal.id)}">Cancelar</button>
          </div>
          <div class="nx-t-3" data-proposal-result></div>
        </div>`;
    })
    .join('');

  box.querySelectorAll('[data-proposal-confirm]').forEach((button) => {
    button.addEventListener('click', () => resolveProposal(button.dataset.proposalConfirm, true, button));
  });
  box.querySelectorAll('[data-proposal-cancel]').forEach((button) => {
    button.addEventListener('click', () => resolveProposal(button.dataset.proposalCancel, false, button));
  });
}

/** Confirmar o cancelar una propuesta.
 *
 *  El backend NO tiene un endpoint propio bajo /api/alerts: las
 *  propuestas viven en la misma tabla de acciones pendientes que el
 *  asistente, así que se confirman por /api/chat/actions/{id}/confirm
 *  y se cancelan por .../cancel. Es el MISMO camino que usa el chat,
 *  y a propósito no se intenta ejecutar nada por otra vía.
 *
 *  El ejecutor devuelve HTTP 200 incluso cuando la acción falla, así
 *  que el veredicto se lee de `success`, nunca del código. */
async function resolveProposal(actionId, confirm, button) {
  if (confirm && !window.confirm('Esta acción modifica el host. ¿Confirmar su ejecución ahora?')) return;
  const card = button.closest('[data-proposal]');
  const resultBox = card ? card.querySelector('[data-proposal-result]') : null;

  if (button) {
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span> Enviando';
  }
  try {
    const result = await apiFetch(
      `/api/chat/actions/${encodeURIComponent(actionId)}/${confirm ? 'confirm' : 'cancel'}`,
      { method: 'POST' }
    );
    if (resultBox) {
      resultBox.innerHTML = verdictHtml(
        result.success ? 'ok' : 'error',
        result.message || (confirm ? 'Confirmada.' : 'Cancelada.'),
        confirm && result.success ? 'La acción ya se ha ejecutado sobre el host.' : ''
      );
    }
    showToast(result.message || (confirm ? 'Propuesta confirmada.' : 'Propuesta cancelada.'), result.success ? 'success' : 'error');
    await fetchProposals();
  } catch (error) {
    if (resultBox) resultBox.innerHTML = verdictHtml('error', 'No se pudo resolver la propuesta', error.message);
    showToast(`No se pudo resolver: ${error.message}`, 'error');
    if (button) {
      button.disabled = false;
      button.textContent = confirm ? 'Confirmar y ejecutar' : 'Cancelar';
    }
  }
}

async function fetchProposals() {
  const box = $('proposals-list');
  if (!box) return;
  try {
    const data = await apiFetch('/api/alerts/remediation?limit=50');
    state.proposals = data.proposals || [];
    renderProposals();
  } catch (error) {
    state.proposals = [];
    box.innerHTML = emptyBlock('No se pudieron leer las propuestas', error.message, true);
  }
}

// ==========================================================
// Histórico
// ------------------------------------------------------------
// El backend ya guarda una serie por métrica con la resolución
// que toca en cada rango (1 min en 24 h, 1 h en 30 d) y entrega
// los puntos como pares {ts, value}. La única decisiones de
// diseño es el EJE: cpu/ram/swap/disco son porcentajes y la
// carga y la temperatura no lo son, así que hay dos escalas y
// cada serie va a la suya. Meter la carga en un 0-100 la
// aplastaría contra el suelo y sería una mentira de escala.
// ==========================================================

/** Etiqueta de un tick del eje X. Con 24 h o menos da la hora; a
 *  partir de 7 d la fecha, porque la hora sola se repite. */
function historyTickLabel(ms, range) {
  const date = new Date(ms);
  if (Number.isNaN(date.getTime())) return '';
  if (range === '7d' || range === '30d') {
    return date.toLocaleDateString('es-ES', { day: '2-digit', month: '2-digit' });
  }
  return date.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit', hour12: false });
}

/** Rango → resolución esperada. Solo es para el texto de la cabecera:
 *  la resolución real la manda el backend en `period`. */
const HISTORY_RANGE_LABEL = { '1h': '1 hora', '24h': '24 horas', '7d': '7 días', '30d': '30 días' };

/** Texto de la cabecera del histórico. Describe lo que hay DENTRO
 *  de la ventana pedida, no la ventana pedida: un host recién
 *  desplegado que pide 24 h puede tener tres horas, y decir
 *  "18:06–18:06" sobre un eje que va de 14:10 a 18:04 sería una
 *  contradicción visible en la misma tarjeta. */
function renderHistoryMeta(payload) {
  const note = $('history-window-note');
  if (!note || !payload) return;

  const points = Object.keys(payload.series || {}).reduce((total, key) => total + (payload.series[key] || []).length, 0);
  if (points === 0) {
    note.textContent = `${HISTORY_RANGE_LABEL[payload.range] || payload.range} · sin muestras guardadas todavía`;
    return;
  }

  const stamps = [];
  Object.keys(payload.series || {}).forEach((key) => {
    (payload.series[key] || []).forEach((point) => stamps.push(point.ts * 1000));
  });
  const from = new Date(Math.min(...stamps));
  const to = new Date(Math.max(...stamps));
  const clock = (date) => date.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit', hour12: false });
  const long = payload.range === '7d' || payload.range === '30d';
  const stamp = (date) => (long
    ? `${date.toLocaleDateString('es-ES', { day: '2-digit', month: '2-digit' })} ${clock(date)}`
    : clock(date));

  note.textContent = `${HISTORY_RANGE_LABEL[payload.range] || payload.range} pedidos · ` +
    `${stamp(from)}–${stamp(to)} con datos · una muestra cada ${payload.period} · ${points} puntos`;
}

function destroyHistoryChart() {
  if (state.historyChart) {
    state.historyChart.destroy();
    state.historyChart = null;
  }
}

function buildHistoryChart(payload) {
  const canvas = $('history-chart');
  if (!canvas || typeof Chart === 'undefined') return;
  destroyHistoryChart();

  const selected = state.historyMetrics.filter((key) => METRIC_META[key]);
  const hasOther = selected.some((key) => !METRIC_META[key].percent);

  const datasets = selected.map((key) => {
    const meta = METRIC_META[key];
    const color = metricColor(key);
    const points = (payload.series && payload.series[key]) || [];
    return {
      label: meta.label,
      // `normalized` sólo es correcto con los puntos ordenados por x.
      data: points
        .map((point) => ({ x: point.ts * 1000, y: Number.isFinite(point.value) ? point.value : null }))
        .sort((a, b) => a.x - b.x),
      borderColor: color,
      backgroundColor: chartAreaFill(color, 0.1),
      fill: false,
      tension: 0.25,
      borderWidth: 1.5,
      borderJoinStyle: 'round',
      // Huecos donde el host no teve telemetría: se deja la línea
      // rota en vez de unir dos puntos que no son continuos.
      spanGaps: false,
      // Igual que en la gráfica del Resumen: un único punto visible
      // para que el gráfico no arranque como una cuadrícula vacía.
      pointRadius,
      pointHoverRadius: 4,
      pointHoverBorderWidth: 2,
      pointHoverBorderColor: CHART_COLORS.surface,
      pointHoverBackgroundColor: color,
      yAxisID: meta.percent ? 'y' : 'y2'
    };
  });

  // El dominio del eje se ajusta a los DATOS, no a la ventana
  // pedida. Una Raspberry recién desplegada tiene tres horas de
  // histórico y pide 24: encajar el eje en la ventana dejaría tres
  //quarters del lienzo vacíos y la serie aplastada contra el borde
  // derecho. La ventana pedida sigue escrita en la cabecera, que es
  // donde se consulta. Sin ningún punto se cae a los límites del
  // backend para que el eje siga siendo un intervalo de tiempo.
  const allPoints = datasets.flatMap((dataset) => dataset.data);
  const domain = allPoints.length
    ? {
      min: Math.min(...allPoints.map((point) => point.x)),
      max: Math.max(...allPoints.map((point) => point.x))
    }
    : { min: (payload.from_ts || 0) * 1000, max: (payload.to_ts || 0) * 1000 };
  // Un único punto no define un intervalo: se le da un margen de un
  // minuto a cada lado para que la línea tenga alguna longitud.
  if (domain.max - domain.min < 60000) {
    domain.min -= 30000;
    domain.max += 30000;
  }

  state.historyChart = new Chart(canvas, {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      // Eje X lineal en milisegundos: con un eje temporal de
      // Chart.js haría falta un adaptador de fechas y aquí no hay
      // dependencias nuevas; se formatea el tick a mano.
      parsing: false,
      normalized: true,
      interaction: { intersect: false, mode: 'index' },
      layout: { padding: { top: 4, right: 4 } },
      scales: {
        y: {
          type: 'linear',
          position: 'left',
          min: 0,
          max: 100,
          display: true,
          border: { display: false },
          ticks: {
            color: CHART_COLORS.inkQuiet,
            stepSize: 25,
            padding: 8,
            callback: (value) => `${value}%`
          },
          grid: {
            color: hexToRgba(CHART_COLORS.ink, 0.07),
            borderDash: [3, 4],
            drawTicks: false
          }
        },
        y2: {
          type: 'linear',
          position: 'right',
          display: hasOther,
          border: { display: false },
          ticks: {
            color: CHART_COLORS.inkQuiet,
            padding: 8,
            maxTicksLimit: 6
          },
          // La escala derecha comparte la estética pero no su
          // cuadrícula: dos rejillas cruzadas son ruido.
          grid: { display: false }
        },
        x: {
          type: 'linear',
          border: { display: false },
          ticks: {
            color: CHART_COLORS.inkQuiet,
            maxTicksLimit: 6,
            maxRotation: 0,
            padding: 8,
            callback: (value) => historyTickLabel(value, payload.range)
          },
          grid: { display: false },
          min: domain.min,
          max: domain.max
        }
      },
      plugins: {
        // La leyenda la pone la fila de casillas de la cabecera.
        legend: { display: false },
        tooltip: {
          backgroundColor: CHART_COLORS.surface,
          titleColor: CHART_COLORS.inkQuiet,
          bodyColor: CHART_COLORS.ink,
          borderColor: CHART_COLORS.border,
          borderWidth: 1,
          cornerRadius: 6,
          padding: 10,
          boxWidth: 8,
          boxHeight: 8,
          boxPadding: 4,
          usePointStyle: true,
          titleFont: { family: CHART_COLORS.mono, size: 11 },
          bodyFont: { family: CHART_COLORS.mono, size: 12 },
          callbacks: {
            title: (items) => {
              if (!items.length) return '';
              const date = new Date(items[0].parsed.x);
              const day = date.toLocaleDateString('es-ES', { day: '2-digit', month: '2-digit' });
              return `${day} ${date.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit', hour12: false })}`;
            },
            label: (ctx) => {
              const key = state.historyMetrics[ctx.datasetIndex];
              const meta = METRIC_META[key] || {};
              const value = ctx.parsed.y;
              if (value === null || value === undefined) return `${ctx.dataset.label}  sin dato`;
              const digits = meta.digits === undefined ? 2 : meta.digits;
              return `${ctx.dataset.label}  ${trimNumber(value, digits)}${meta.unit ? ` ${meta.unit}` : ''}`;
            }
          }
        }
      }
    }
  });
}

/**
 * Consulta del histórico con el host de la cabecera delante. Con un
 * host remoto se pide SU histórico: el backend guarda por host_id, así
 * que la misma llamada devuelve otra serie sin cambiar de endpoint.
 * El enlace de CSV comparte la misma condición (ver `historyCsvHref`).
 */
function historyQuery() {
  const base = `/api/metrics-history?range=${encodeURIComponent(state.historyRange)}`;
  return isRemoteScope() ? `${base}&host_id=${encodeURIComponent(String(state.hostId))}` : base;
}

/**
 * Enlace de exportación: misma ventana y mismo host que el gráfico.
 * `host_id` se manda también aquí a propósito — el endpoint de CSV
 * todavía no lo acepta y lo ignora, así que el fichero sale de este
 * servidor sea cual sea el host elegido; por eso la interfaz lo dice
 * con `updateHistoryCsvNote()` en vez de dejar que el enlace aparente
 * una exportación del host remoto.
 */
function historyCsvHref() {
  return `/api/metrics-export.csv?range=${encodeURIComponent(state.historyRange)}` +
    (isRemoteScope() ? `&host_id=${encodeURIComponent(String(state.hostId))}` : '');
}

/**
 * Veredicto sobre el enlace de CSV. `disabled` de verdad (no un
 * `pointer-events: none`): un enlace que parece Download y no
 * descarga nada es peor que un enlace que explica por qué.
 */
function updateHistoryCsvNote() {
  const note = $('history-csv-note');
  const csv = $('history-csv');
  const remote = isRemoteScope();
  if (note) {
    note.textContent = remote
      ? 'La exportación CSV sale de este servidor, no del host remoto'
      : '';
    note.classList.toggle('hidden', !remote);
  }
  if (csv) {
    csv.setAttribute('aria-disabled', String(remote));
    csv.title = remote
      ? 'El endpoint de exportación todavía solo cubre el servidor del panel.'
      : 'Descarga el rango completo en CSV';
    csv.classList.toggle('is-disabled', remote);
  }
}

async function fetchHistory() {
  const empty = $('history-empty');
  const canvas = $('history-chart');
  if (!canvas) return;

  if (state.historyMetrics.length === 0) {
    destroyHistoryChart();
    if (empty) {
      empty.textContent = 'No hay ninguna métrica seleccionada. Activa al menos una casilla de arriba.';
      empty.className = 'nx-status nx-status--warn';
      empty.classList.remove('hidden');
    }
    return;
  }
  if (empty) empty.classList.add('hidden');

  const metrics = state.historyMetrics.join(',');
  try {
    const payload = await apiFetch(`${historyQuery()}&metric=${encodeURIComponent(metrics)}`);
    state.historyPayload = payload;
    updateHistoryCsvNote();
    renderHistoryMeta(payload);
    buildHistoryChart(payload);
  } catch (error) {
    destroyHistoryChart();
    if (empty) {
      empty.textContent = `No se pudo leer el histórico: ${error.message}`;
      empty.className = 'nx-status nx-status--error';
      empty.classList.remove('hidden');
    }
  }
}

function setHistoryRange(range) {
  state.historyRange = range;
  document.querySelectorAll('[data-range]').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.range === range));
  });
  // El enlace de exportación comparte el rango: si no, el CSV
  // descargado sería de otra ventana que la que se está mirando.
  const csv = $('history-csv');
  if (csv) csv.href = historyCsvHref();
  updateHistoryCsvNote();
  fetchHistory();
}

function toggleHistoryMetric(key) {
  const index = state.historyMetrics.indexOf(key);
  if (index >= 0) {
    // Se permite quitar todas: el panel avisa y no dibuja nada en
    // lugar de decidir por su cuenta qué serie era la importante.
    state.historyMetrics.splice(index, 1);
  } else {
    state.historyMetrics.push(key);
    state.historyMetrics.sort((a, b) => METRIC_KEYS.indexOf(a) - METRIC_KEYS.indexOf(b));
  }
  document.querySelectorAll('[data-pick]').forEach((button) => {
    button.setAttribute('aria-pressed', String(state.historyMetrics.includes(button.dataset.pick)));
  });
  fetchHistory();
}

// ==========================================================
// Monitores
// ------------------------------------------------------------
// Sondas externas. El backend mide; aquí se declara qué se
// mide, con qué frecuencia y con qué margen de error. El
// formulario cambia de campos según el tipo porque los tres
// contratos son distintos: http mira una URL, tcp un puerto y
// ssl un certificado.
// ==========================================================

/** Muestra u oculta los campos según el tipo de sonda. El puerto
 *  solo tiene sentido en tcp y ssl (en http lo lleva la URL), y el
 *  código esperado y el texto esperado solo existen en http. */
function applyMonitorKindFields() {
  const kind = $('mon-kind').value;
  const http = kind === 'http';
  const port = !http;
  if ($('mon-port-field')) $('mon-port-field').classList.toggle('hidden', !port);
  if ($('mon-expected-field')) $('mon-expected-field').classList.toggle('hidden', !http);
  if ($('mon-keyword-field')) $('mon-keyword-field').classList.toggle('hidden', !http);
  if ($('mon-target')) {
    $('mon-target').placeholder = http
      ? 'https://ejemplo.com/salud'
      : (kind === 'ssl' ? 'ejemplo.com' : '192.168.1.10');
  }
}

function renderMonitors() {
  const tbody = $('monitors-table');
  if (!tbody) return;

  if (state.monitors.length === 0) {
    tbody.innerHTML = emptyCell(11, 'Sin monitores', 'No hay ninguna sonda definida. Crea la primera con "Nuevo monitor".');
    return;
  }

  tbody.innerHTML = state.monitors
    .map((monitor) => {
      const up = String(monitor.status || '').toLowerCase();
      const ledClass = up === 'up' ? LED_CLASS.ok : up === 'down' ? LED_CLASS.crit : LED_CLASS.unknown;
      const target = monitor.port ? `${monitor.target}:${monitor.port}` : monitor.target;
      const error = monitor.last_error
        ? `<p class="nx-hint nx-t-2">${escapeHtml(monitor.last_error)}</p>`
        : '';
      const ssl = monitor.ssl_days_left === null || monitor.ssl_days_left === undefined
        ? '—'
        : `${monitor.ssl_days_left} d`;
      return `
        <tr>
          <td><i class="nx-led ${ledClass}" aria-hidden="true"></i></td>
          <td class="nx-cell-strong">${escapeHtml(monitor.name)}</td>
          <td class="nx-cond">${escapeHtml(monitor.kind)}</td>
          <td class="nx-cond">${escapeHtml(target)}<p class="nx-hint nx-t-2">cada ${escapeHtml(formatSecondsShort(monitor.interval_s))} · máx ${escapeHtml(String(monitor.timeout_s))} s</p>${error}</td>
          <td class="nx-cell-num">${monitor.latency_ms === null || monitor.latency_ms === undefined ? '—' : `${escapeHtml(trimNumber(monitor.latency_ms, 1))} ms`}</td>
          <td class="nx-cell-quiet">${escapeHtml(formatStamp(monitor.last_check))}</td>
          <td class="nx-cell-num nx-cell-quiet">${escapeHtml(formatUptimePercent(monitor.uptime_24h))}</td>
          <td class="nx-cell-num nx-cell-quiet">${escapeHtml(formatUptimePercent(monitor.uptime_7d))}</td>
          <td class="nx-cell-num nx-cell-quiet">${escapeHtml(formatUptimePercent(monitor.uptime_30d))}</td>
          <td class="nx-cell-num nx-cell-quiet">${escapeHtml(ssl)}</td>
          <td class="nx-cell-actions">
            <span class="nx-btn-row">
              <button class="btn btn-ghost btn-sm" data-monitor-detail="${escapeHtml(String(monitor.id))}">Latidos</button>
              <button class="btn btn-ghost btn-sm" data-monitor-edit="${escapeHtml(String(monitor.id))}">Editar</button>
              <button class="btn btn-danger btn-sm" data-monitor-delete="${escapeHtml(String(monitor.id))}" data-monitor-name="${escapeHtml(monitor.name)}">Eliminar</button>
            </span>
          </td>
        </tr>`;
    })
    .join('');

  tbody.querySelectorAll('[data-monitor-detail]').forEach((button) => {
    button.addEventListener('click', () => openMonitorDetail(Number(button.dataset.monitorDetail)));
  });
  tbody.querySelectorAll('[data-monitor-edit]').forEach((button) => {
    button.addEventListener('click', () => openMonitorForm(Number(button.dataset.monitorEdit)));
  });
  tbody.querySelectorAll('[data-monitor-delete]').forEach((button) => {
    button.addEventListener('click', () => deleteMonitor(Number(button.dataset.monitorDelete), button.dataset.monitorName));
  });
}

function openMonitorForm(monitorId) {
  const wrap = $('monitor-form-wrap');
  const form = $('monitor-form');
  if (!wrap || !form) return;

  const monitor = monitorId ? state.monitors.find((item) => item.id === monitorId) : null;
  state.editingMonitorId = monitor ? monitor.id : null;

  form.reset();
  $('mon-interval').value = '60';
  $('mon-timeout').value = '5';
  $('mon-enabled').value = '1';

  if (monitor) {
    $('mon-name').value = monitor.name;
    $('mon-kind').value = monitor.kind;
    $('mon-target').value = monitor.target;
    $('mon-port').value = monitor.port === null || monitor.port === undefined ? '' : String(monitor.port);
    $('mon-interval').value = String(monitor.interval_s);
    $('mon-timeout').value = String(monitor.timeout_s);
    $('mon-expected').value = monitor.expected_status === null || monitor.expected_status === undefined
      ? '' : String(monitor.expected_status);
    $('mon-keyword').value = monitor.keyword || '';
    $('mon-enabled').value = Number(monitor.enabled) ? '1' : '0';
    $('mon-form-note').textContent = `Editando el monitor ${monitor.id}`;
  } else {
    $('mon-form-note').textContent = 'Se creará un monitor nuevo';
  }

  applyMonitorKindFields();
  $('mon-status').classList.add('hidden');
  wrap.classList.remove('hidden');
  $('mon-name').focus();
}

function closeMonitorForm() {
  state.editingMonitorId = null;
  const wrap = $('monitor-form-wrap');
  if (wrap) wrap.classList.add('hidden');
}

async function submitMonitor(event) {
  event.preventDefault();
  const kind = $('mon-kind').value;
  const target = $('mon-target').value.trim();
  const portRaw = $('mon-port').value.trim();

  if (!target) {
    setFormStatus('mon-status', 'Indica el objetivo que hay que comprobar.', 'warn');
    return;
  }
  if (kind !== 'http' && !portRaw && !/:\d+$/.test(target)) {
    setFormStatus('mon-status', 'Un monitor de puerto necesita el puerto: ponlo en el campo o al final del objetivo (host:puerta).', 'warn');
    return;
  }

  const payload = {
    name: $('mon-name').value.trim(),
    kind,
    target,
    interval_s: Number($('mon-interval').value),
    timeout_s: Number($('mon-timeout').value),
    enabled: $('mon-enabled').value === '1'
  };
  if (kind !== 'http') payload.port = portRaw ? Number(portRaw) : null;
  if (kind === 'http') {
    const expected = $('mon-expected').value.trim();
    const keyword = $('mon-keyword').value.trim();
    if (expected) payload.expected_status = Number(expected);
    if (keyword) payload.keyword = keyword;
  }

  const editing = state.editingMonitorId;
  try {
    if (editing) {
      await mutate(`/api/monitors/${editing}`, 'PATCH', payload, `Monitor "${payload.name}" guardado.`);
    } else {
      await mutate('/api/monitors', 'POST', payload, `Monitor "${payload.name}" creado.`);
    }
    closeMonitorForm();
    await fetchMonitors();
  } catch (error) {
    setFormStatus('mon-status', `No se pudo guardar: ${error.message}`, 'error');
  }
}

async function deleteMonitor(monitorId, name) {
  if (!window.confirm(`¿Eliminar el monitor "${name}"? Se borrarán también sus latidos.`)) return;
  try {
    const result = await mutate(`/api/monitors/${monitorId}`, 'DELETE', null);
    showToast(result.message || 'Monitor eliminado.', 'success');
    if (state.editingMonitorId === monitorId) closeMonitorForm();
    if (state.openMonitorId === monitorId) closeMonitorDetail();
    await fetchMonitors();
  } catch (error) {
    showToast(`No se pudo eliminar: ${error.message}`, 'error');
  }
}

async function fetchMonitors() {
  const tbody = $('monitors-table');
  if (!tbody) return;
  try {
    const data = await apiFetch('/api/monitors');
    state.monitors = data.monitors || [];
    renderMonitors();
  } catch (error) {
    state.monitors = [];
    tbody.innerHTML = emptyCell(11, 'No se pudieron leer los monitores', error.message, true);
  }
}

// ------------------- Latidos de un monitor -------------------
function closeMonitorDetail() {
  state.openMonitorId = null;
  const panel = $('monitor-detail');
  if (panel) panel.classList.add('hidden');
  if (state.heartbeatsChart) {
    state.heartbeatsChart.destroy();
    state.heartbeatsChart = null;
  }
}

function renderHeartbeatFacts(monitor, points) {
  const box = $('monitor-detail-facts');
  if (!box) return;
  const latencies = points
    .map((point) => Number(point.latency_ms))
    .filter((value) => Number.isFinite(value));
  const down = points.filter((point) => !Number(point.up)).length;
  const average = latencies.length
    ? latencies.reduce((total, value) => total + value, 0) / latencies.length
    : null;

  const cells = [
    ['Estado', monitorStatusBadge(monitor.status)],
    ['Comprobaciones', escapeHtml(String(points.length))],
    ['Latencia media', average === null ? '—' : `${escapeHtml(trimNumber(average, 1))} ms`],
    ['Latencia máxima', latencies.length ? `${escapeHtml(trimNumber(Math.max(...latencies), 1))} ms` : '—'],
    ['Caídas', down === 0
      ? '<span class="nx-sr-st nx-sr-st--ok">Ninguna</span>'
      : `<span class="nx-sr-st nx-sr-st--crit">${escapeHtml(String(down))}</span>`],
    ['Disponibilidad 24 h', escapeHtml(formatUptimePercent(monitor.uptime_24h))]
  ];
  box.innerHTML = cells
    .map(([label, value]) => `<div><p class="nx-kv-label">${label}</p><p class="nx-kv-value">${value}</p></div>`)
    .join('');
}

function buildHeartbeatsChart(points) {
  const canvas = $('heartbeats-chart');
  if (!canvas || typeof Chart === 'undefined') return;
  if (state.heartbeatsChart) {
    state.heartbeatsChart.destroy();
    state.heartbeatsChart = null;
  }

  state.heartbeatsChart = new Chart(canvas, {
    type: 'line',
    data: {
      datasets: [{
        label: 'Latencia',
        data: points
          .filter((point) => Number.isFinite(Number(point.latency_ms)))
          .map((point) => ({ x: point.ts * 1000, y: Number(point.latency_ms), up: Number(point.up) })),
        borderColor: CHART_COLORS.cpu,
        backgroundColor: chartAreaFill(CHART_COLORS.cpu, 0.1),
        fill: true,
        tension: 0.2,
        borderWidth: 1.5,
        borderJoinStyle: 'round',
        spanGaps: false,
        // Un punto solo donde la comprobación falló: la caída es el
        // dato importante y no puede depender de leer la línea.
        pointRadius: (ctx) => (ctx.raw && ctx.raw.up === 0 ? 3.5 : 0),
        pointBackgroundColor: CHART_COLORS.ink,
        pointBorderColor: '#ff6b6b',
        pointHoverRadius: 5
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      parsing: false,
      normalized: true,
      interaction: { intersect: false, mode: 'nearest' },
      layout: { padding: { top: 4, right: 4 } },
      scales: {
        y: {
          beginAtZero: true,
          border: { display: false },
          ticks: {
            color: CHART_COLORS.inkQuiet,
            maxTicksLimit: 5,
            padding: 8,
            callback: (value) => `${value} ms`
          },
          grid: {
            color: hexToRgba(CHART_COLORS.ink, 0.07),
            borderDash: [3, 4],
            drawTicks: false
          }
        },
        x: {
          type: 'linear',
          border: { display: false },
          ticks: {
            color: CHART_COLORS.inkQuiet,
            maxTicksLimit: 5,
            maxRotation: 0,
            padding: 8,
            callback: (value) => {
              const date = new Date(value);
              const time = date.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit', hour12: false });
              return state.heartbeatHours > 48
                ? `${date.toLocaleDateString('es-ES', { day: '2-digit', month: '2-digit' })} ${time}`
                : time;
            }
          },
          grid: { display: false }
        }
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: CHART_COLORS.surface,
          titleColor: CHART_COLORS.inkQuiet,
          bodyColor: CHART_COLORS.ink,
          borderColor: CHART_COLORS.border,
          borderWidth: 1,
          cornerRadius: 6,
          padding: 10,
          boxWidth: 8,
          boxHeight: 8,
          usePointStyle: true,
          titleFont: { family: CHART_COLORS.mono, size: 11 },
          bodyFont: { family: CHART_COLORS.mono, size: 12 },
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}  ${trimNumber(ctx.parsed.y, 1)} ms${ctx.raw && ctx.raw.up === 0 ? '  · caída' : ''}`
          }
        }
      }
    }
  });
}

async function openMonitorDetail(monitorId) {
  const panel = $('monitor-detail');
  const monitor = state.monitors.find((item) => item.id === monitorId);
  if (!panel || !monitor) return;

  state.openMonitorId = monitorId;
  $('monitor-detail-title').textContent = monitor.name;
  panel.classList.remove('hidden');

  const facts = $('monitor-detail-facts');
  if (facts) facts.innerHTML = '<p class="nx-hint">Cargando comprobaciones…</p>';

  try {
    const data = await apiFetch(`/api/monitors/${monitorId}/heartbeats?hours=${state.heartbeatHours}`);
    const points = data.points || [];
    renderHeartbeatFacts(monitor, points);
    buildHeartbeatsChart(points);
  } catch (error) {
    if (facts) facts.innerHTML = `<p class="nx-status nx-status--error">${escapeHtml(error.message)}</p>`;
  }
}

async function reloadMonitorDetail() {
  if (state.openMonitorId === null) return;
  await openMonitorDetail(state.openMonitorId);
}

function setHeartbeatHours(hours) {
  state.heartbeatHours = hours;
  document.querySelectorAll('[data-hb-hours]').forEach((button) => {
    button.setAttribute('aria-pressed', String(Number(button.dataset.hbHours) === hours));
  });
  reloadMonitorDetail();
}

// ==========================================================
// Auditoría
// ------------------------------------------------------------
// Un log encadenado por hash. Cada fila guarda el hash de la
// anterior, así que el backend puede verificar que nadie la
// reescribió por el camino; esa verificación se enseña arriba,
// porque un registro sin integrity check no es más que un archivo
// de texto con marcas de tiempo.
// ==========================================================

/** Nombre de las acciones vistas hasta ahora, para el <datalist> del
 *  filtro. El backend sólo acepta coincidencia EXACTA, así que
 *  ofrecer los nombres reales evita que se escriban a mano. */
function refreshAuditActionHints(events) {
  const list = $('audit-action-list');
  if (!list) return;
  const seen = Object.create(null);
  const names = [];
  events.forEach((event) => {
    if (!event.action || seen[event.action]) return;
    seen[event.action] = true;
    names.push(event.action);
  });
  list.innerHTML = names
    .slice(0, 40)
    .map((name) => `<option value="${escapeHtml(name)}"></option>`)
    .join('');
}

function renderAuditChain(chain, total) {
  const note = $('audit-chain-note');
  const detail = $('audit-chain-detail');
  const valid = chain && chain.valid;

  if (note) {
    note.innerHTML = valid
      ? `${badgeHtml('badge-running', 'Cadena íntegra')}`
      : `${badgeHtml('badge-error', 'Cadena rota')}`;
  }
  if (detail) {
    const error = chain && chain.error ? ` · ${chain.error}` : '';
    detail.textContent = chain
      ? `Verificadas ${chain.events} entradas encadenadas de ${total}${error}`
      : 'El backend no ha informado del estado de la cadena.';
  }
}

function renderAuditEvents() {
  const tbody = $('audit-table');
  if (!tbody) return;

  const page = state.auditPage || { count: 0, total: 0, limit: state.audit.limit, offset: state.audit.offset, events: [], chain: {} };

  if (!page.events || page.events.length === 0) {
    const filtering = state.audit.action || state.audit.since;
    tbody.innerHTML = emptyCell(
      7,
      filtering ? 'Ninguna entrada coincide' : 'Registro vacío',
      filtering
        ? 'Ninguna entrada cumple el filtro. El nombre de la acción tiene que ser exacto, tal y como aparece en la columna "Acción".'
        : 'Todavía no hay entradas registradas.'
    );
  } else {
    tbody.innerHTML = page.events
      .map((entry) => {
        const details = entry.details && Object.keys(entry.details).length
          ? Object.entries(entry.details)
            .map(([key, value]) => `${key}: ${typeof value === 'object' ? JSON.stringify(value) : String(value)}`)
            .join(' · ')
          : '';
        // El hash se enseña recortado: los 64 caracteres completos
        // no caben en una columna y no se pueden verificar a ojo.
        const hash = entry.hash || '';
        const short = hash ? `${hash.slice(0, 10)}…` : '—';
        const failed = String(entry.result || '').toLowerCase() !== 'ok';
        return `
          <tr>
            <td class="nx-cell-num nx-cell-quiet">${escapeHtml(String(entry.seq ?? '—'))}</td>
            <td class="nx-cell-quiet">${escapeHtml(formatStamp(entry.ts))}</td>
            <td class="nx-cell-strong mono">${escapeHtml(entry.action)}</td>
            <td class="nx-cond">${escapeHtml(entry.actor || 'system')}</td>
            <td class="nx-cell-quiet">${escapeHtml(entry.target || '—')}${details ? `<p class="nx-hint nx-t-2">${escapeHtml(details)}</p>` : ''}</td>
            <td>${failed ? badgeHtml('badge-error', entry.result) : badgeHtml('badge-running', entry.result || 'ok')}</td>
            <td class="nx-cell-actions"><span class="nx-cond" title="${escapeHtml(hash)}">${escapeHtml(short)}</span></td>
          </tr>`;
      })
      .join('');
  }

  const total = page.total || 0;
  const limit = page.limit || state.audit.limit;
  const offset = page.offset || 0;
  const first = total === 0 ? 0 : offset + 1;
  const last = Math.min(offset + limit, total);

  const range = $('audit-range');
  if (range) {
    range.textContent = total === 0
      ? 'Sin entradas que mostrar'
      : `Entrada ${first}–${last} de ${total}${state.audit.action ? ` · acción ${state.audit.action}` : ''}${state.audit.since ? ` · desde ${state.audit.since}` : ''}`;
  }
  renderAuditChain(page.chain, total);

  const prev = $('audit-prev');
  const next = $('audit-next');
  if (prev) prev.disabled = offset <= 0;
  if (next) next.disabled = offset + limit >= total;
}

async function fetchAudit() {
  const tbody = $('audit-table');
  if (!tbody) return;
  const params = new URLSearchParams();
  params.set('limit', String(state.audit.limit));
  params.set('offset', String(state.audit.offset));
  if (state.audit.action) params.set('action', state.audit.action);
  if (state.audit.since) params.set('since', state.audit.since);

  try {
    const data = await apiFetch(`/api/audit?${params.toString()}`);
    state.auditPage = data;
    state.audit.total = data.total || 0;
    refreshAuditActionHints(data.events || []);
    renderAuditEvents();
  } catch (error) {
    state.auditPage = { events: [], total: 0, limit: state.audit.limit, offset: 0, chain: {} };
    tbody.innerHTML = emptyCell(7, 'No se pudo leer el registro', error.message, true);
  }
}

/** Cualquier cambio de filtro vuelve a la primera página: si no,
 *  con el offset a la mitad el usuario ve una página vacía y cree
 *  que el filtro no devolvió nada. */
function resetAuditAndFetch() {
  state.audit.offset = 0;
  fetchAudit();
}

// ==========================================================
// Sistema
// ------------------------------------------------------------
// Puertos, actualizaciones y SMART. Los tres comparten el mismo
// contrato de disponibilidad: el backend responde 200 con
// `available:false` y un motivo cuando no puede leerlos, así que
// el panel enseña ese motivo en lugar de una tabla vacía que
// parecería "no hay nada" cuando en realidad es "no se pudo mirar".
// ============================================================

/** Cuerpo estándar de un bloque que puede no estar disponible. */
function unavailableHtml(label, reason) {
  return emptyCell(1, label, reason || 'El backend no ha dado un motivo.', true);
}

/**
 * Estado vacío para un contenedor que NO es una tabla.
 *
 * `emptyCell()` devuelve una fila porque casi todos los estados
 * vacíos del panel viven en un <tbody>. Meter una <tr> dentro de un
 * <div> —como en el bloque de propuestas— hace que el navegador la
 * desenvuelva: la celda desaparece, `.empty-row` deja de aplicar y
 * la etiqueta se queda con el cuerpo de un título en vez del
 * micro-rótulo de 10,5px. Ésta es la variante de bloque, con las
 * mismas clases para que las dos se vean igual.
 */
function emptyBlock(label, text, isError = false) {
  return `<div class="empty-row"><span class="nx-empty${isError ? ' nx-empty--error' : ''}">` +
    `<span class="nx-empty-label">${escapeHtml(label)}</span>` +
    `<span class="nx-empty-text">${escapeHtml(text)}</span>` +
    '</span></div>';
}

function renderPorts(data) {
  const tbody = $('ports-table');
  const note = $('ports-note');
  if (!tbody) return;

  if (data.available === false) {
    if (note) note.textContent = '';
    tbody.innerHTML = unavailableHtml('Puertos no disponibles', data.reason);
    return;
  }

  const ports = data.ports || [];
  if (note) {
    // `partial` es la diferencia entre "no hay más puertos" y "hay
    // más pero no pude resolver el proceso de alguno": merece
    // conspicibilidad porque son dos lecturas del mismo inventario.
    note.textContent = `${data.count} sockets en escucha` +
      (data.partial ? ' · lectura parcial: algunos procesos no se pudieron resolver' : '');
  }

  if (ports.length === 0) {
    tbody.innerHTML = emptyCell(6, 'Sin sockets en escucha', 'Este host no tiene ningún puerto abierto.');
    return;
  }

  tbody.innerHTML = ports
    .map((port) => `
      <tr>
        <td class="nx-cond">${escapeHtml(port.family)}</td>
        <td class="nx-cond">${escapeHtml(port.ip)}</td>
        <td class="nx-cell-num nx-cell-strong">${escapeHtml(String(port.port))}</td>
        <td class="nx-cond">${escapeHtml(port.status)}</td>
        <td>${escapeHtml(port.process || '—')}</td>
        <td class="nx-cell-num nx-cell-quiet">${port.pid === null || port.pid === undefined ? '—' : escapeHtml(String(port.pid))}</td>
      </tr>`)
    .join('');
}

async function fetchPorts() {
  const tbody = $('ports-table');
  if (!tbody) return;
  try {
    renderPorts(
      isRemoteScope()
        ? await hostCommand('system', { action: 'ports' }, 60)
        : await apiFetch('/api/system/ports')
    );
  } catch (error) {
    tbody.innerHTML = unavailableHtml('No se pudieron leer los puertos', error.message);
  }
}

function renderUpdates(data) {
  const box = $('updates-body');
  if (!box) return;

  if (data.available === false) {
    box.innerHTML = emptyBlock('Actualizaciones no consultables', data.reason || 'El backend no ha dado un motivo.', true);
    return;
  }

  const packages = data.packages || [];
  if (packages.length === 0) {
    box.innerHTML = emptyBlock('Sistema actualizado', 'No hay ningún paquete pendiente de instalar.');
    return;
  }

  box.innerHTML = `<div class="nx-table-scroll nx-table-scroll--short">
    <table class="data-table">
      <thead><tr><th scope="col">Paquete</th><th scope="col">Versión disponible</th></tr></thead>
      <tbody>${packages.map((pkg) => `
        <tr>
          <td class="nx-cell-strong mono">${escapeHtml(pkg.package)}</td>
          <td class="nx-cond">${escapeHtml(pkg.version || '—')}</td>
        </tr>`).join('')}</tbody>
    </table>
  </div>`;
}

async function fetchUpdates() {
  const box = $('updates-body');
  if (!box) return;
  box.innerHTML = '<div class="nx-card-body"><p class="nx-hint">Consultando el gestor de paquetes…</p></div>';
  try {
    renderUpdates(
      isRemoteScope()
        ? await hostCommand('system', { action: 'updates' }, 90)
        : await apiFetch('/api/system/updates')
    );
  } catch (error) {
    box.innerHTML = emptyBlock('No se pudieron consultar las actualizaciones', error.message, true);
  }
}

function renderSmart(data) {
  const box = $('smart-body');
  if (!box) return;

  if (data.available === false) {
    box.innerHTML = emptyBlock('SMART no disponible', data.reason || 'El backend no ha dado un motivo.', true);
    return;
  }

  const disks = data.disks || [];
  if (disks.length === 0) {
    box.innerHTML = emptyBlock('Sin discos', 'No se ha detectado ningún disco al que preguntar por SMART.');
    return;
  }

  box.innerHTML = `<div class="nx-devices">${disks.map((disk) => {
    // `health` es texto del smartctl ("PASSED"/"FAILED"); se decide
    // el tono por el contenido, no por la caja: un "FAILED" en rojo
    // y cualquier otra cosa como dato, no como aprobación.
    const health = String(disk.health || '').toUpperCase();
    const failed = health.includes('FAIL') || health.includes('ERROR');
    const badge = disk.health
      ? (failed ? badgeHtml('badge-error', disk.health) : badgeHtml('badge-running', disk.health))
      : '<span class="nx-cell-quiet">sin dato</span>';
    const cells = [
      ['Salud', badge],
      ['Temperatura', disk.temperature === null || disk.temperature === undefined ? '—' : `${escapeHtml(trimNumber(disk.temperature, 1))} °C`],
      ['Desgaste', disk.wear === null || disk.wear === undefined ? '—' : `${escapeHtml(trimNumber(disk.wear, 0))} %`],
      ['Sectores reasignados', disk.reallocated_sectors === null || disk.reallocated_sectors === undefined ? '—' : escapeHtml(String(disk.reallocated_sectors))]
    ];
    return `<div class="nx-device">
      <div class="nx-device-head">
        <span class="nx-device-name">${escapeHtml(disk.device)}</span>
        ${disk.model ? `<span class="nx-device-model">${escapeHtml(disk.model)}</span>` : ''}
        ${badgeHtml('badge-stopped', disk.source)}
      </div>
      <div class="nx-kv nx-t-2">${cells.map(([label, value]) => `<div><p class="nx-kv-label">${label}</p><p class="nx-kv-value">${value}</p></div>`).join('')}</div>
    </div>`;
  }).join('')}</div>`;
}

async function fetchSmart() {
  const box = $('smart-body');
  if (!box) return;
  box.innerHTML = '<div class="nx-card-body"><p class="nx-hint">Consultando SMART…</p></div>';
  try {
    renderSmart(
      isRemoteScope()
        ? await hostCommand('system', { action: 'smart' }, 120)
        : await apiFetch('/api/system/smart')
    );
  } catch (error) {
    box.innerHTML = emptyBlock('No se pudo consultar SMART', error.message, true);
  }
}

// ==========================================================
// Seguridad
// ------------------------------------------------------------
// Tres surfaces con socializing distinto: un token que se emite
// y NO se puede volver a leer, sesiones que se pueden cortar desde
// aquí, y un segundo factor que se puede dejar preparado a medias.
// El detalle que comparten es que los tres muestran secretos:
// por eso hay un plano de lectura de una sola vez, no campos.
// ==========================================================

/** Plano de lectura de un valor que el servidor no volverá a entregar. */
function showSecret(boxId, value) {
  const node = $(boxId);
  if (node) node.textContent = value;
}

async function fetchTokens() {
  const tbody = $('tokens-table');
  if (!tbody) return;
  try {
    const data = await apiFetch('/api/tokens');
    const tokens = data.tokens || [];
    if (tokens.length === 0) {
      tbody.innerHTML = emptyCell(8, 'Sin tokens', 'No hay tokens de API. Crea uno para integrar el panel con otras herramientas.');
      return;
    }
    tbody.innerHTML = tokens
      .map((token) => `
        <tr>
          <td class="nx-cell-strong">${escapeHtml(token.name)}</td>
          <td class="nx-cond">${escapeHtml(token.prefix)}…</td>
          <td class="nx-cond">${escapeHtml(token.scope)}</td>
          <td class="nx-cell-quiet">${escapeHtml(formatStamp(token.created_at))}</td>
          <td class="nx-cell-quiet">${token.last_used_at ? escapeHtml(formatStamp(token.last_used_at)) : 'Nunca'}</td>
          <td class="nx-cell-quiet">${token.expires_at ? escapeHtml(formatStamp(token.expires_at)) : 'Nunca'}</td>
          <td>${token.active ? badgeHtml('badge-running', 'Activo') : badgeHtml('badge-stopped', 'Revocado')}</td>
          <td class="nx-cell-actions">
            ${token.active
              ? `<button class="btn btn-danger btn-sm" data-token-revoke="${escapeHtml(String(token.id))}" data-token-name="${escapeHtml(token.name)}">Revocar</button>`
              : '<span class="nx-cell-quiet">—</span>'}
          </td>
        </tr>`)
      .join('');

    tbody.querySelectorAll('[data-token-revoke]').forEach((button) => {
      button.addEventListener('click', () => revokeToken(Number(button.dataset.tokenRevoke), button.dataset.tokenName));
    });
  } catch (error) {
    tbody.innerHTML = emptyCell(8, 'No se pudieron leer los tokens', error.message, true);
  }
}

async function submitToken(event) {
  event.preventDefault();
  const name = $('token-name').value.trim();
  if (!name) {
    setFormStatus('token-status', 'Ponle un nombre al token para saber luego quién lo usa.', 'warn');
    return;
  }
  const ttl = $('token-ttl').value;
  const payload = { name, scope: $('token-scope').value };
  // El backend rechaza `ttl_days: 0` con un 422: "sin caducidad" se
  // envía como null, no como cero.
  payload.ttl_days = ttl === '' ? null : Number(ttl);

  try {
    const result = await apiFetch('/api/tokens', { method: 'POST', body: JSON.stringify(payload) });
    // `token` es el único lugar del sistema donde aparece el valor
    // en claro: se enseña y no se vuelve a pedir.
    $('token-reveal-name').textContent = result.name;
    showSecret('token-reveal-value', result.token);
    $('token-reveal').classList.remove('hidden');
    $('token-form-wrap').classList.add('hidden');
    $('token-form').reset();
    showToast(`Token "${result.name}" creado. Cópialo ahora.`, 'success');
    await fetchTokens();
  } catch (error) {
    setFormStatus('token-status', `No se pudo crear el token: ${error.message}`, 'error');
  }
}

async function revokeToken(tokenId, name) {
  if (!window.confirm(`¿Revocar el token "${name}"? Quien lo use dejará de poder llamar al panel.`)) return;
  try {
    const result = await mutate(`/api/tokens/${tokenId}`, 'DELETE', null);
    showToast(result.message || 'Token revocado.', 'success');
    await fetchTokens();
  } catch (error) {
    showToast(`No se pudo revocar: ${error.message}`, 'error');
  }
}

// ------------------- Sesiones -------------------
async function fetchSessions() {
  const tbody = $('sessions-table');
  if (!tbody) return;
  try {
    const data = await apiFetch('/api/security/sessions');
    const sessions = data.sessions || [];

    const badge = $('sessions-count');
    if (badge) badge.textContent = sessions.length > 0 ? ` ${sessions.length}` : '';

    if (sessions.length === 0) {
      tbody.innerHTML = emptyCell(8, 'Sin sesiones', 'No hay ninguna sesión registrada.');
      return;
    }

    tbody.innerHTML = sessions
      .map((session) => `
        <tr>
          <td class="nx-cond">${escapeHtml(String(session.id).slice(0, 12))}…</td>
          <td class="nx-cell-strong">${escapeHtml(session.username)}</td>
          <td class="nx-cell-quiet">${escapeHtml(formatUnix(session.created_at))}</td>
          <td class="nx-cell-quiet">${escapeHtml(formatUnix(session.last_seen))}</td>
          <td class="nx-cell-quiet">${escapeHtml(formatUnix(session.expires_at))}</td>
          <td class="nx-cell-num nx-cell-quiet">${escapeHtml(formatSecondsShort(session.idle_seconds))}</td>
          <td>${session.is_current
            ? badgeHtml('badge-running', 'Esta sesión')
            : (session.expired ? badgeHtml('badge-stopped', 'Caducada') : badgeHtml('badge-warn', 'Activa'))}</td>
          <td class="nx-cell-actions">
            ${session.is_current
              ? '<span class="nx-cell-quiet">Usa "Salir"</span>'
              : `<button class="btn btn-danger btn-sm" data-session-revoke="${escapeHtml(session.id)}"${session.expired ? ' disabled' : ''}>Revocar</button>`}
          </td>
        </tr>`)
      .join('');

    tbody.querySelectorAll('[data-session-revoke]').forEach((button) => {
      button.addEventListener('click', () => revokeSession(button.dataset.sessionRevoke));
    });
  } catch (error) {
    tbody.innerHTML = emptyCell(8, 'No se pudieron leer las sesiones', error.message, true);
  }
}

/** Revocar una sesión ajena. El backend responde 409 si se le pide
 *  la propia —de ahí que el botón ni siquiera exista en esa fila—,
 *  y el mensaje se enseña tal cual para no inventar una explicación. */
async function revokeSession(sessionId) {
  if (!window.confirm('¿Revocar esta sesión? Su navegador tendrá que volver a identificarse.')) return;
  try {
    const result = await mutate(`/api/security/sessions/${encodeURIComponent(sessionId)}`, 'DELETE', null);
    showToast(result.message || 'Sesión revocada.', 'success');
    await fetchSessions();
  } catch (error) {
    showToast(`No se pudo revocar la sesión: ${error.message}`, 'error');
    await fetchSessions();
  }
}

// ------------------- Segundo factor -------------------
async function fetchTwoFactor() {
  const box = $('2fa-status-body');
  if (!box) return;
  try {
    const status = await apiFetch('/api/security/2fa');
    state.twoFactor = status;

    const cards = [
      ['Estado', status.enabled
        ? badgeHtml('badge-running', 'Activado')
        : (status.configured ? badgeHtml('badge-warn', 'Preparado sin activar') : badgeHtml('badge-stopped', 'Desactivado'))],
      ['Secreto almacenado', status.configured ? 'Sí' : 'No'],
      ['Códigos de recuperación', `${status.recovery_left} sin usar`]
    ];

    box.innerHTML = `
      <div class="nx-kv">${cards.map(([label, value]) => `<div><p class="nx-kv-label">${label}</p><p class="nx-kv-value">${value}</p></div>`).join('')}</div>
      <p class="nx-hint nx-t-3">${status.enabled
        ? 'Cada inicio de sesión pedirá el código de tu aplicación además de la contraseña.'
        : 'Sin segundo factor, la contraseña es la única puerta del panel.'}</p>
      <div class="nx-actions nx-t-3">
        ${status.enabled ? '' : '<button id="2fa-setup" type="button" class="btn btn-primary btn-sm">Preparar segundo factor</button>'}
      </div>`;

    const setupButton = $('2fa-setup');
    if (setupButton) setupButton.addEventListener('click', startTwoFactorSetup);

    // Cuando hay un secreto preparado sin activar, el botón que
    // limpia el estado tiene que decirlo: no es "desactivar" nada,
    // es descartar una preparación que el servidor aún guarda.
    const disableWrap = $('2fa-enable-wrap');
    const disableButton = $('2fa-disable');
    if (disableWrap && disableButton) {
      const discard = status.configured && !status.enabled;
      disableWrap.classList.toggle('hidden', !status.enabled && !discard);
      disableButton.textContent = discard ? 'Descartar preparación' : 'Desactivar segundo factor';
    }
  } catch (error) {
    box.innerHTML = `<p class="nx-status nx-status--error">${escapeHtml(error.message)}</p>`;
  }
}

async function startTwoFactorSetup() {
  try {
    const result = await apiFetch('/api/security/2fa/setup', { method: 'POST', body: '{}' });
    // `secret`, `otpauth_url` y `recovery_codes` sólo se devuelven
    // aquí. No se dibuja un QR: la URL otpauth se da como texto
    // seleccionable, que es lo único que hace falta para teclear o
    // pegar en la aplicación de autenticación.
    state.twoFactorSecret = result.secret;
    state.twoFactorRecovery = result.recovery_codes || [];
    showSecret('2fa-secret', result.secret);
    showSecret('2fa-otpauth', result.otpauth_url);
    // Cada código va en su propio <span>: un <pre> con saltos de
    // línea es UN solo elemento de rejilla, así que la columna doble
    // del CSS no se aplicaba y los diez códigos caían apilados.
    $('2fa-codes').innerHTML = (result.recovery_codes || [])
      .map((code) => `<span>${escapeHtml(code)}</span>`)
      .join('');
    $('2fa-enable-status').classList.add('hidden');
    $('2fa-code').value = '';
    $('2fa-setup-wrap').classList.remove('hidden');
    showToast('Secreto generado. Guárdalo antes de activar nada.', 'success');
    // La tarjeta de estado tiene que enterarse: a partir de aquí hay
    // un secreto preparado, y el botón de limpieza pasa a existir.
    await fetchTwoFactor();
  } catch (error) {
    setFormStatus('2fa-enable-status', `No se pudo preparar: ${error.message}`, 'error');
    $('2fa-setup-wrap').classList.remove('hidden');
  }
}

async function enableTwoFactor(event) {
  event.preventDefault();
  const code = $('2fa-code').value.trim();
  if (!/^\d{6}$/.test(code)) {
    setFormStatus('2fa-enable-status', 'El código son los 6 dígitos que muestra tu aplicación de autenticación.', 'warn');
    return;
  }
  try {
    const result = await apiFetch('/api/security/2fa/enable', {
      method: 'POST',
      body: JSON.stringify({ code })
    });
    showToast(result.message || 'Segundo factor activado.', 'success');
    state.twoFactorSecret = null;
    state.twoFactorRecovery = null;
    $('2fa-setup-wrap').classList.add('hidden');
    await fetchTwoFactor();
  } catch (error) {
    setFormStatus('2fa-enable-status', `No se pudo activar: ${error.message}`, 'error');
  }
}

async function disableTwoFactor(event) {
  event.preventDefault();
  const password = $('2fa-password').value;
  if (!password) {
    setFormStatus('2fa-disable-status', 'Escribe la contraseña actual para confirmar.', 'warn');
    return;
  }
  try {
    const result = await apiFetch('/api/security/2fa/disable', {
      method: 'POST',
      body: JSON.stringify({ password })
    });
    showToast(result.message || 'Segundo factor desactivado.', 'success');
    state.twoFactorSecret = null;
    state.twoFactorRecovery = null;
    $('2fa-password').value = '';
    $('2fa-enable-wrap').classList.add('hidden');
    $('2fa-setup-wrap').classList.add('hidden');
    await fetchTwoFactor();
  } catch (error) {
    setFormStatus('2fa-disable-status', `No se pudo desactivar: ${error.message}`, 'error');
  }
}

/** Vuelve al panel de estado sin tocar nada: el secreto preparado
 *  sigue en el servidor y la tarjeta de estado lo dice. */
function discardTwoFactorSetup() {
  state.twoFactorSecret = null;
  state.twoFactorRecovery = null;
  $('2fa-setup-wrap').classList.add('hidden');
  $('2fa-enable-wrap').classList.remove('hidden');
  $('2fa-password').focus();
  fetchTwoFactor();
}

// ==========================================================
// Contenedores: detalle e inventario Docker
// ==========================================================

/** Registros y consumo en vivo de un contenedor. El backend devuelve
 *  `stats` como un volcado opaco de líneas de /proc sin contrato de
 *  claves, así que se pinta como texto monoespaciado y no como una
 *  tabla con campos inventados. */
async function openContainerDetail(containerId) {
  const panel = $('container-detail');
  if (!panel) return;
  state.containerDetailId = containerId;

  const list = state.containerStates[containerId];
  $('container-detail-title').textContent = list ? `${list.name}` : containerId;
  const stats = $('container-stats');
  if (stats) stats.innerHTML = '<p class="nx-hint">Cargando consumo…</p>';
  $('container-logs').textContent = 'Cargando registros…';
  panel.classList.remove('hidden');

  const tail = $('container-logs-tail') ? Number($('container-logs-tail').value) : 100;
  try {
    const [statsData, logsData] = isRemoteScope()
      ? await Promise.all([
        hostCommand('docker', { action: 'stats', container_id: containerId }),
        hostCommand('docker', { action: 'logs', container_id: containerId, tail }, 60)
      ])
      : await Promise.all([
        apiFetch(`/api/containers/${encodeURIComponent(containerId)}/stats`),
        apiFetch(`/api/containers/${encodeURIComponent(containerId)}/logs?tail=${tail}`)
      ]);

    const entries = statsData && statsData.stats && typeof statsData.stats === 'object'
      ? Object.entries(statsData.stats)
      : [];
    if (stats) {
      stats.innerHTML = entries.length
        ? entries.map(([key, value]) => `
          <div>
            <p class="nx-kv-label">${escapeHtml(key)}</p>
            <p class="nx-kv-value">${escapeHtml(typeof value === 'object' ? JSON.stringify(value) : String(value))}</p>
          </div>`).join('')
        : '<p class="nx-hint">Este contenedor no ha devuelto métricas de consumo.</p>';
    }

    const lines = (logsData && logsData.lines) || [];
    $('container-logs').textContent = lines.length
      ? lines.join('\n')
      : (logsData && logsData.success === false
        ? 'No se pudieron leer los registros de este contenedor.'
        : 'Sin registros.');
  } catch (error) {
    if (stats) stats.innerHTML = `<p class="nx-status nx-status--error">${escapeHtml(error.message)}</p>`;
    $('container-logs').textContent = `No se pudo leer el contenedor: ${error.message}`;
  }
}

function closeContainerDetail() {
  state.containerDetailId = null;
  const panel = $('container-detail');
  if (panel) panel.classList.add('hidden');
}

/** Pinta un bloque del inventario Docker. Los tres endpoints
 *  comparten `{available, reason, count, <items>}` y en un host sin
 *  Docker los tres responden con available:false y el motivo. */
function renderDockerVolumes(data) {
  const tbody = $('docker-volumes-table');
  const counter = $('docker-volumes-count');
  if (!tbody) return;
  if (data.available === false) {
    if (counter) counter.textContent = '';
    tbody.innerHTML = unavailableHtml('Volúmenes no disponibles', data.reason);
    return;
  }
  const volumes = data.volumes || [];
  if (counter) counter.textContent = `${volumes.length} volúmenes`;
  if (volumes.length === 0) {
    tbody.innerHTML = emptyCell(4, 'Sin volúmenes', 'Este host no tiene volúmenes Docker.');
    return;
  }
  tbody.innerHTML = volumes.map((volume) => `
    <tr>
      <td class="nx-cell-strong mono">${escapeHtml(volume.name)}</td>
      <td class="nx-cond">${escapeHtml(volume.driver || '—')}</td>
      <td class="nx-cell-quiet">${escapeHtml(volume.scope || '—')}</td>
      <td class="nx-cond nx-cell-quiet">${escapeHtml(volume.mountpoint || '—')}</td>
    </tr>`).join('');
}

function renderDockerNetworks(data) {
  const tbody = $('docker-networks-table');
  const counter = $('docker-networks-count');
  if (!tbody) return;
  if (data.available === false) {
    if (counter) counter.textContent = '';
    tbody.innerHTML = unavailableHtml('Redes no disponibles', data.reason);
    return;
  }
  const networks = data.networks || [];
  if (counter) counter.textContent = `${networks.length} redes`;
  if (networks.length === 0) {
    tbody.innerHTML = emptyCell(4, 'Sin redes', 'Este host no tiene redes Docker.');
    return;
  }
  tbody.innerHTML = networks.map((network) => `
    <tr>
      <td class="nx-cell-strong mono">${escapeHtml(network.name)}</td>
      <td class="nx-cond">${escapeHtml(network.driver || '—')}</td>
      <td class="nx-cell-quiet">${escapeHtml(network.scope || '—')}</td>
      <td class="nx-cell-actions">${network.internal ? badgeHtml('badge-stopped', 'Interna') : badgeHtml('badge-running', 'Enrutable')}</td>
    </tr>`).join('');
}

function renderDockerImages(data) {
  const tbody = $('docker-images-table');
  const counter = $('docker-images-count');
  if (!tbody) return;
  if (data.available === false) {
    if (counter) counter.textContent = '';
    tbody.innerHTML = unavailableHtml('Imágenes no disponibles', data.reason);
    return;
  }
  const images = data.images || [];
  if (counter) counter.textContent = `${images.length} imágenes`;
  if (images.length === 0) {
    tbody.innerHTML = emptyCell(4, 'Sin imágenes', 'Este host no tiene imágenes Docker descargadas.');
    return;
  }
  tbody.innerHTML = images.map((image) => `
    <tr>
      <td class="nx-cond">${escapeHtml(image.short_id || String(image.id || '').slice(0, 12))}</td>
      <td class="nx-cell-strong mono">${escapeHtml((image.tags && image.tags.length) ? image.tags.join(', ') : '<sin etiqueta>')}</td>
      <td class="nx-cell-num">${escapeHtml(formatBytes(image.size_bytes))}</td>
      <td class="nx-cell-quiet">${escapeHtml(formatStamp(image.created))}</td>
    </tr>`).join('');
}

/** Bytes → "1,4 GiB". Formato español: coma decimal, como en el
 *  resto del panel y como escribe el usuario. */
function formatBytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return '0 B';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let index = 0;
  let size = value;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${index === 0 ? size : size.toFixed(1).replace('.', ',')} ${units[index]}`;
}

async function fetchDockerInventory() {
  if (isRemoteScope()) {
    const reason = 'El agente todavía no expone el inventario de Docker (volúmenes, imágenes y redes).';
    renderDockerVolumes({ available: false, reason });
    renderDockerNetworks({ available: false, reason });
    renderDockerImages({ available: false, reason });
    return;
  }
  try {
    const [volumes, networks, images] = await Promise.all([
      apiFetch('/api/docker/volumes'),
      apiFetch('/api/docker/networks'),
      apiFetch('/api/docker/images')
    ]);
    renderDockerVolumes(volumes);
    renderDockerNetworks(networks);
    renderDockerImages(images);
  } catch (error) {
    const message = error.message;
    renderDockerVolumes({ available: false, reason: message });
    renderDockerNetworks({ available: false, reason: message });
    renderDockerImages({ available: false, reason: message });
  }
}

/** Limpiar imágenes sin uso. El backend exige `confirm:true` en el
 *  propio cuerpo de la petición, así que hay dos puertas: el
 *  `window.confirm` de aquí es UX, y el `confirm` del servidor es la
 *  que de verdad impide el borrado por accidente. */
async function pruneDockerImages() {
  if (!window.confirm('¿Limpiar las imágenes Docker sin uso?\n\nSe borrarán las imágenes que no use ningún contenedor. No se tocarán los volúmenes.')) return;
  try {
    const result = isRemoteScope()
      ? await hostCommand(
        'docker',
        { action: 'prune', images: true, volumes: false, confirm: true },
        120
      )
      : await apiFetch('/api/docker/prune', {
        method: 'POST',
        body: JSON.stringify({ images: true, volumes: false, confirm: true })
      });
    // Un fallo de limpieza llega como 200 con success:false, así que
    // el mensaje que se enseña es el del backend y no un "error".
    showToast(result.message || (result.success ? 'Imágenes sin uso eliminadas.' : 'No se pudo limpiar.'), result.success ? 'success' : 'error');
    await Promise.all([fetchDockerInventory(), fetchContainers()]);
  } catch (error) {
    showToast(`No se pudo limpiar: ${error.message}`, 'error');
  }
}

// ==========================================================
// Andamiaje compartido de las vistas nuevas
// ------------------------------------------------------------
// Alertas, histórico, monitores, auditoría, sistema y seguridad
// son seis tablas más de la misma consola. Repiten el mismo
// trabajo de siempre —leer, pintar una fila densa, avisar con un
// toast, dejar un formulario limpio— y ese trabajo vive aquí
// para que los seis módulos no lo reescriban seis veces.
// ==========================================================

/** Datos de las seis métricas series. `unit` es lo que va en la
 *  etiqueta del eje y en el tooltip; `scaleMax` fija el tope del
 *  gráfico, no un umbral de alarma (los umbrales viven en SENSORS). */
const METRIC_META = {
  cpu:   { label: 'CPU',         unit: '%',    digits: 1, percent: true,  scaleMax: 100 },
  ram:   { label: 'RAM',         unit: '%',    digits: 1, percent: true,  scaleMax: 100 },
  swap:  { label: 'Swap',        unit: '%',    digits: 1, percent: true,  scaleMax: 100 },
  disk:  { label: 'Disco',       unit: '%',    digits: 1, percent: true,  scaleMax: 100 },
  load1: { label: 'Carga',       unit: '/núc.', digits: 2, percent: false, scaleMax: 2 },
  temp:  { label: 'Temperatura', unit: '°C',   digits: 1, percent: false, scaleMin: 20, scaleMax: 90 }
};
const METRIC_KEYS = ['cpu', 'ram', 'swap', 'disk', 'load1', 'temp'];
/** Cada serie toma su color del token CSS, no de una constante JS:
 *  si el token se ajusta, la gráfica se ajusta con él y no hay dos
 *  listas que mantener en paralelo. */
const METRIC_TOKEN = {
  cpu: '--nx-series-cpu',
  ram: '--nx-series-ram',
  swap: '--nx-series-swap',
  disk: '--nx-series-disk',
  load1: '--nx-series-load',
  temp: '--nx-series-temp'
};

function metricColor(key) {
  const token = METRIC_TOKEN[key];
  if (!token) return CHART_COLORS.inkMuted;
  const value = window.getComputedStyle(document.documentElement).getPropertyValue(token).trim();
  return value || CHART_COLORS.inkMuted;
}

function metricLabel(key) {
  const meta = METRIC_META[key];
  return meta ? meta.label : key;
}

/** Quita ceros sin valor: 90.0 → "90", 0.75 → "0.75". Una columna de
 *  umbrales con "90.0" repetido no se lee de un vistazo. */
function trimNumber(value, digits) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return Number(number.toFixed(digits === undefined ? 2 : digits)).toString();
}

/** Segundos como "45 s" / "5 min" / "2 h 30 min". Distinto de
 *  formatUptime(), que es para "el host lleva X días arriba". */
function formatSecondsShort(totalSeconds) {
  const seconds = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

/** ISO-8601 → "17/10/2026 15:06:02". El backend devuelve siempre UTC
 *  con offset explícito, así que la conversión es directa. */
function formatStamp(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  return date.toLocaleString('es-ES', { hour12: false });
}

/** Marca de tiempo UNIX en segundos → el mismo formato. Las sesiones
 *  devuelven números, no ISO: por eso existe esta y no se reutiliza
 *  la de arriba. */
function formatUnix(seconds) {
  const number = Number(seconds);
  if (!Number.isFinite(number) || number <= 0) return '—';
  return formatStamp(new Date(number * 1000).toISOString());
}

/** Porcentaje de disponibilidad: 99.5 → "99.5 %", null → "—". */
function formatUptimePercent(value) {
  const number = Number(value);
  if (value === null || value === undefined || !Number.isFinite(number)) return '—';
  return `${trimNumber(number, 1)} %`;
}

/** Etiqueta de estado de la tabla de monitores → pastilla. */
function monitorStatusBadge(status) {
  const map = {
    up: ['badge-running', 'Operativo'],
    down: ['badge-error', 'Caído'],
    unknown: ['badge-stopped', 'Sin datos']
  };
  const entry = map[String(status || '').toLowerCase()];
  return entry ? badgeHtml(entry[0], entry[1]) : badgeHtml('badge-stopped', status || 'Desconocido');
}

/** Severidad de un evento de alerta → pastilla. */
function alertSeverityBadge(severity) {
  const value = String(severity || '').toLowerCase();
  if (value === 'crit' || value === 'critical') return badgeHtml('badge-error', 'Crítico');
  if (value === 'warn' || value === 'warning') return badgeHtml('badge-warn', 'Aviso');
  return badgeHtml('badge-stopped', value || '—');
}

/** Estado de un evento de alerta → pastilla. */
function alertStatusBadge(status) {
  const map = {
    open: ['badge-error', 'Abierto'],
    acked: ['badge-warn', 'Reconocido'],
    closed: ['badge-running', 'Cerrado']
  };
  const entry = map[String(status || '').toLowerCase()];
  return entry ? badgeHtml(entry[0], entry[1]) : badgeHtml('badge-stopped', status || '—');
}

/** Texto de estado en una línea de formulario. `kind` es 'ok',
 *  'warn' o 'error'; sin `kind` sale como línea informativa. */
function setFormStatus(id, message, kind) {
  const box = $(id);
  if (!box) return;
  const suffix = kind === 'ok' ? ' nx-status--ok'
    : kind === 'error' ? ' nx-status--error'
      : kind === 'warn' ? ' nx-status--warn' : '';
  box.textContent = message;
  box.className = `nx-status${suffix}`;
  box.classList.remove('hidden');
}

/** Bloque de veredicto de una acción puntual (probar canal,
 *  confirmar propuesta). Se usa donde el resultado de la llamada es
 *  el contenido, no un toast que se pierde a los 4 segundos. */
function verdictHtml(kind, text, detail) {
  const cls = kind === 'ok' ? 'nx-verdictbox--ok' : kind === 'error' ? 'nx-verdictbox--error' : 'nx-verdictbox--info';
  return `<div class="nx-verdictbox ${cls}">
    <p>${escapeHtml(text)}</p>
    ${detail ? `<p class="nx-verdictbox-text">${escapeHtml(detail)}</p>` : ''}
  </div>`;
}

/** Copia al portapapeles con reserva: en un contexto no seguro la API
 *  de portapapeles está bloqueada, y entonces lo útil es seleccionar
 *  el texto para que el Ctrl+C manual funcione. */
async function copyToClipboard(text, label) {
  try {
    await navigator.clipboard.writeText(text);
    showToast(`${label} copiado al portapapeles.`, 'success');
    return;
  } catch (_error) {
    // Sigue al plan B.
  }
  const helper = document.createElement('textarea');
  helper.value = text;
  helper.setAttribute('readonly', '');
  helper.style.position = 'fixed';
  helper.style.left = '-9999px';
  document.body.appendChild(helper);
  helper.select();
  let copied = false;
  try { copied = document.execCommand('copy'); } catch (_error) { copied = false; }
  helper.remove();
  showToast(
    copied ? `${label} copiado al portapapeles.` : `No se pudo copiar ${label.toLowerCase()}: selecciónalo a mano.`,
    copied ? 'success' : 'error'
  );
}

/** Ejecuta un POST/PATCH/DELETE y avisa del resultado. Envuelve el
 *  `try/catch` que, si no, se repite nueve veces en los formularios. */
async function mutate(path, method, body, successMessage) {
  const result = await apiFetch(path, { method, body: JSON.stringify(body) });
  if (successMessage) showToast(successMessage, 'success');
  return result;
}

// ------------------- Sub-pestañas -------------------
/** Rieles de sub-pestaña. Al abrir una vista se dispara su carga una
 *  sola vez por sesión; los recuentos de la cabecera se rellenan al
 *  mismo tiempo, así que abrir "Eventos" no puede mostrar "0" al
 *  lado de una lista llena. */
const SUBTAB_LOADERS = {
  alerts: {
    rules: fetchAlertRules,
    events: fetchAlertEvents,
    channels: fetchChannels,
    maintenance: fetchWindows,
    proposals: fetchProposals
  },
  security: {
    tokens: fetchTokens,
    sessions: fetchSessions,
    twofactor: fetchTwoFactor
  }
};

function showSubtab(group, name) {
  const tab = document.querySelector(`.nx-subtab[data-subgroup="${group}"][data-subtab="${name}"]`);
  if (!tab) return;
  document.querySelectorAll(`.nx-subtab[data-subgroup="${group}"]`).forEach((button) => {
    const active = button.dataset.subtab === name;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-selected', String(active));
  });
  document.querySelectorAll(`.nx-subpanel[data-subgroup="${group}"]`).forEach((panel) => {
    panel.classList.toggle('hidden', panel.dataset.subpanel !== name);
  });
  const loader = (SUBTAB_LOADERS[group] || {})[name];
  if (loader && !state.loaded[`${group}.${name}`]) {
    state.loaded[`${group}.${name}`] = true;
    loader();
  }
}

/** Agrega el boton "guardar" de un formulario a un panel de estado y
 *  devuelve un manejador que ya gestiona el botón durante la llamada.
 *  El texto y el estado del propio `<button>` son el único indicador
 *  de progreso que el resto del panel usa. */
function withBusy(button, busyText, task) {
  return async (event) => {
    if (event) event.preventDefault();
    const node = $(button);
    if (!node) return task(event);
    const original = node.textContent;
    node.disabled = true;
    node.innerHTML = `<span class="spinner"></span> ${busyText}`;
    try {
      // El evento se pasa: los manejadores de `submit` hacen
      // `event.preventDefault()`, y sin él la página intentaría
      // navegar al terminar la llamada.
      return await task(event);
    } finally {
      node.disabled = false;
      node.textContent = original;
    }
  };
}

// ==========================================================
// Flota de hosts
// ------------------------------------------------------------
// El hub conoce su flota: el host local (id 1, el servidor donde
// corre el panel) y los remotos, que existen porque su agente se
// enroló y manda latido. Esta sección hace tres cosas:
//
//   1. Muestra el inventario y deja dar de alta, renombrar,
//      anotar, deshabilitar y dar de baja un host.
//   2. Publica el token de enrolamiento y el comando exacto que
//      se pega en la máquina destino.
//   3. Publica el selector global de la cabecera: la elección se
//      recuerda y dirige el Resumen y el Histórico.
//
// Regla de honestidad que atraviesa todo el bloque: el panel nunca
// presenta como remoto lo que es local ni al revés. Por eso el
// veredicto se recalcula contra el host elegido, las secciones que
// solo kaben local lo dicen, y el CSV exporta lo que realmente
// exporta.
// ==========================================================

/** Clave de la elección persistente del host. */
const HOST_STORAGE_KEY = 'nexus.hostId';

/** Métricas que el agente informa; el orden es el de SENSORS. */
const REMOTE_METRICS = 'cpu,ram,swap,disk,load1,temp';

/**
 * ¿Estamos mirando un host remoto? La respuesta decide qué pinta el
 * Resumen, cómo se recalcula el veredicto y qué avisos aparecen en las
 * secciones locales. Un solo predicado para las tres cosas: si
 * divergieran, la pantalla mentiría en algún sitio.
 */
function isRemoteScope() {
  return Boolean(state.host && !Number(state.host.is_local));
}

/** Host local del hub. El id lo fija el backend (db.LOCAL_HOST_ID). */
function localHost() {
  return state.hosts.find((host) => Number(host.is_local) === 1) || null;
}

/** Fila del inventario que corresponde a la selección actual. */
function selectedHost() {
  return state.hosts.find((host) => Number(host.id) === Number(state.hostId)) || null;
}

/** Severidad de un host en la tabla: los hosts usan la paleta del panel. */
function hostLedClass(host) {
  if (!Number(host.enabled)) return LED_CLASS.unknown;
  return host.online ? LED_CLASS.ok : LED_CLASS.crit;
}

/** Texto del estado: el vocabulary de fleet.computed_status. */
function hostStatusLabel(host) {
  if (!Number(host.enabled)) return 'Deshabilitado';
  return host.online ? 'En línea' : 'Sin latido';
}

/** "ahora" / "hace 12 s" a partir de los segundos que da el backend. */
function secondsSinceSeenLabel(seconds) {
  if (seconds === null || seconds === undefined) return 'nunca';
  return formatRelative(Date.now() - Number(seconds) * 1000);
}

/** Fecha corta de enrolamiento, o «—» si no hay. */
function hostEnrolledLabel(stamp) {
  if (!stamp) return '—';
  const date = new Date(stamp);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleDateString('es-ES', { day: '2-digit', month: '2-digit', year: '2-digit' });
}

// --------------------------- Inventario ---------------------------

/**
 * Tabla de la flota. Cada fila lleva su LED, su nombre con el
 * ámbito explícito (LOCAL o el id del agente), el heartbeat en
 * relativo y las acciones. El host local no trae botón de borrar:
 * el backend lo rechaza con 409 y un botón que solo puede fallar no
 * se pinta — se explica con el texto de al lado.
 */
function renderHosts() {
  const tbody = $('hosts-table');
  if (!tbody) return;

  if (!state.hosts.length) {
    tbody.innerHTML = emptyCell(9, 'Sin hosts', 'El hub no tiene ningún host en el inventario todavía.');
    renderHostSummary();
    return;
  }

  tbody.innerHTML = state.hosts
    .map((host) => {
      const isLocal = Number(host.is_local) === 1;
      const notes = host.notes ? `<span class="nx-host-note">${escapeHtml(host.notes)}</span>` : '';
      return `
        <tr${state.editingHostId === Number(host.id) ? ' aria-current="true"' : ''}>
          <td><i class="nx-led ${hostLedClass(host)}" aria-hidden="true"></i><span class="nx-sr-only">${escapeHtml(hostStatusLabel(host))}</span></td>
          <td class="nx-cell-strong nx-cell-name">${escapeHtml(host.name)}
            <span class="nx-host-tag ${isLocal ? 'nx-host-tag--local' : ''}">${isLocal ? 'local' : `agente #${escapeHtml(String(host.id))}`}</span>
            ${notes}
          </td>
          <td class="nx-cell-quiet">${escapeHtml(host.hostname || '—')}</td>
          <td>${escapeHtml(host.os || '—')}</td>
          <td class="nx-cell-num">${escapeHtml(host.agent_version || '—')}</td>
          <td class="nx-cell-quiet">${escapeHtml(hostEnrolledLabel(host.enrolled_at))}</td>
          <td class="nx-cell-num">${escapeHtml(secondsSinceSeenLabel(host.seconds_since_seen))}</td>
          <td>${Number(host.enabled) ? 'Sí' : 'No'}</td>
          <td class="nx-cell-actions">
            <span class="nx-btn-row">
              <button class="btn btn-ghost btn-sm" data-hosts-edit="${escapeHtml(String(host.id))}">Editar</button>
              ${isLocal
                ? '<button class="btn btn-danger btn-sm" disabled aria-disabled="true" title="El host local es este servidor: no se puede eliminar ni deshabilitar.">Eliminar</button>'
                : `<button class="btn btn-danger btn-sm" data-hosts-delete="${escapeHtml(String(host.id))}" data-hosts-name="${escapeHtml(host.name)}">Eliminar</button>`}
            </span>
          </td>
        </tr>`;
    })
    .join('');

  renderHostSummary();
}

/** Recuentos de la cabecera de sección y del panel: los dos sitios. */
function renderHostSummary() {
  const total = state.hosts.length;
  const online = state.hosts.filter((host) => host.status === 'online').length;
  const remote = total - (localHost() ? 1 : 0);
  const summary = $('hosts-summary');
  if (summary) {
    summary.textContent = total
      ? `${total} ${plural(total, 'host', 'hosts')} · ${remote} ${plural(remote, 'agente', 'agentes')}`
      : 'Sin inventario';
  }
  const counts = $('hosts-counts');
  if (counts) {
    counts.textContent = total ? `${online} en línea · ${total - online} sin latido` : '—';
  }
}

async function fetchHosts() {
  try {
    const payload = await apiFetch('/api/hosts');
    state.hosts = payload.hosts || [];
    // Si el host guardado ya no existe (se eliminó, o el navegador
    // venía de otro hub), se vuelve al local sin preguntar.
    if (!selectedHost()) state.hostId = localHost() ? Number(localHost().id) : 1;
    state.host = selectedHost();
    renderHosts();
    renderHostSelector();
    renderHostScope();
    if (isRemoteScope()) await renderRemoteSensors();
  } catch (error) {
    const tbody = $('hosts-table');
    if (tbody) {
      tbody.innerHTML = emptyCell(9, 'No se pudo leer la flota', error.message, true);
    }
    console.error('Error obteniendo la flota de hosts:', error.message);
  }
}

// ---------------------------- Edición ----------------------------

/** Abre el formulario en la fila del host (o lo cierra si es el mismo). */
function openHostEditor(hostId) {
  const host = state.hosts.find((item) => Number(item.id) === Number(hostId));
  const wrap = $('hosts-edit-wrap');
  if (!host || !wrap) return;

  if (state.editingHostId === Number(hostId)) {
    closeHostEditor();
    return;
  }
  state.editingHostId = Number(hostId);
  const name = $('hosts-edit-name');
  const notes = $('hosts-edit-notes');
  const enabled = $('hosts-edit-enabled');
  const title = $('hosts-edit-title');
  const note = $('hosts-edit-note');
  if (name) name.value = host.name || '';
  if (notes) notes.value = host.notes || '';
  if (enabled) enabled.value = Number(host.enabled) ? '1' : '0';
  if (title) title.textContent = `Editar ${host.name}`;
  // Deshabilitar es una de las dos palancas que se cambian aquí y
  // una de ellas tiene consecuencias: el texto lo dice.
  if (note) {
    note.textContent = Number(host.is_local)
      ? 'Este es el servidor del panel: su estado no se puede cambiar.'
      : 'Deshabilitar un host revoca su token en la práctica: su agente recibirá 401 en la siguiente llamada.';
  }
  if (enabled) enabled.disabled = Number(host.is_local) === 1;
  wrap.classList.remove('hidden');
  renderHosts();
  if (name) name.focus();
}

function closeHostEditor() {
  const wrap = $('hosts-edit-wrap');
  const status = $('hosts-edit-status');
  state.editingHostId = null;
  if (wrap) wrap.classList.add('hidden');
  if (status) {
    status.textContent = '';
    status.className = 'nx-status hidden';
  }
  renderHosts();
}

async function saveHost(event) {
  event.preventDefault();
  const hostId = state.editingHostId;
  if (hostId === null) return;
  const button = $('hosts-edit-save');
  const status = $('hosts-edit-status');

  const name = ($('hosts-edit-name') || {}).value || '';
  const notes = ($('hosts-edit-notes') || {}).value || '';
  const enabled = ($('hosts-edit-enabled') || {}).value === '1';

  const guard = await withBusy(button, 'Guardando', async () => {
    try {
      await apiFetch(`/api/hosts/${encodeURIComponent(String(hostId))}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, notes, enabled })
      });
      await fetchHosts();
      closeHostEditor();
      showToast('Host actualizado.', 'success');
    } catch (error) {
      if (status) {
        status.textContent = `No se pudo guardar: ${error.message}`;
        status.className = 'nx-status nx-status--error';
        status.classList.remove('hidden');
      }
    }
  })();
  return guard;
}

/** Borrar un host revoca su token y se lleva su histórico. Se confirma. */
async function deleteHost(hostId, hostName) {
  const warning = `¿Eliminar "${hostName}"? Su token queda revocado y se borra su histórico de métricas. ` +
    'Si solo quieres silenciarlo un rato, deshabilítalo en vez de eliminarlo.';
  if (!window.confirm(warning)) return;
  try {
    const result = await apiFetch(`/api/hosts/${encodeURIComponent(String(hostId))}`, { method: 'DELETE' });
    showToast(result.message || 'Host eliminado.', result.success === false ? 'error' : 'success');
    if (Number(state.hostId) === Number(hostId)) selectHost(localHost() ? Number(localHost().id) : 1);
    await fetchHosts();
  } catch (error) {
    showToast(`Error al eliminar: ${error.message}`, 'error');
  }
}

// -------------------------- Enrolamiento -------------------------

/**
 * Comando de enrolamiento. El backend ya devuelve la plantilla
 * exacta en `EnrollmentInfo.command`; aquí solo se sustituye el
 * `https://HUB` de la plantilla por el origen desde el que se está
 * viendo el panel, que es el único dato que el backend no puede
 * conocer. Si algún día la plantilla cambia, esto la sigue.
 */
function enrollmentCommands(info) {
  const origin = window.location.origin;
  const token = String((info && info.token) || '');
  const template = String((info && info.command) || '').trim();
  // agent.py añade "/api/agent" a la URL base: aquí va la raíz del hub, no /api.
  const hubBase = origin;

  const enroll = template
    ? template.replace(/\bhttps?:\/\/HUB\b/g, hubBase)
    : `python3 agent.py enroll --hub ${hubBase} --enroll-token ${token} --name NOMBRE`;

  const interpreter = template.includes('python') ? template.split(/\s+/)[0] : 'python3';
  const run = `${interpreter} agent.py run --hub ${hubBase}`;

  const install = `sudo bash deploy/agent/install-agent.sh \\\n` +
    `    --hub ${hubBase} \\\n` +
    `    --enroll-token ${token} \\\n` +
    `    --name "NOMBRE"`;

  return { token, source: (info && info.source) || '', enroll, run, install, warning: (info && info.warning) || '' };
}

/** Pinta el panel de alta: token, comandos y el aviso de secreto. */
function renderEnrollment() {
  const commands = enrollmentCommands(state.enrollment);
  const put = (id, text) => {
    const node = $(id);
    if (node) node.textContent = text || '—';
  };
  put('hosts-enroll-token', commands.token);
  put('hosts-enroll-command', commands.enroll);
  put('hosts-run-command', commands.run);
  put('hosts-install-command', commands.install);
  const source = $('hosts-enroll-source');
  if (source) {
    source.textContent = commands.source === 'entorno'
      ? 'token fijado en el entorno del hub'
      : 'token generado y persistente';
  }
  const warning = $('hosts-enroll-warning');
  if (warning) warning.textContent = commands.warning;
}

async function fetchEnrollment() {
  try {
    state.enrollment = await apiFetch('/api/hosts/enrollment');
    renderEnrollment();
  } catch (error) {
    console.error('Error obteniendo el token de enrolamiento:', error.message);
    const token = $('hosts-enroll-token');
    if (token) token.textContent = `No se pudo leer el token: ${error.message}`;
  }
}

/**
 * Copia al portapapeles con confirmación EN EL BOTÓN. Es distinta de
 * `copyToClipboard()` (que avisa con un toast) porque aquí el texto
 * copiado es un comando que el usuario va a pegar en otra máquina: el
 * aviso tiene que durar lo suficiente para que se vea y tiene que
 * desaparecer solo, sin dejar una pila de toasts en pantalla.
 *
 * La API del portapapeles no está en todos los navegadores y sin
 * contexto seguro falla: hay respaldo con `execCommand` y, si tampoco
 * está, el texto se selecciona para copiarlo a mano.
 */
async function copyWithFeedback(text, button, idleLabel) {
  const original = button ? button.textContent : '';
  const mark = (label, kind) => {
    if (!button) return;
    button.textContent = label;
    button.classList.toggle('is-copied', kind === 'ok');
    window.setTimeout(() => {
      button.textContent = original || idleLabel;
      button.classList.remove('is-copied');
    }, 1600);
  };

  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const scratch = document.createElement('textarea');
      scratch.value = text;
      scratch.setAttribute('readonly', '');
      scratch.style.position = 'fixed';
      scratch.style.opacity = '0';
      document.body.appendChild(scratch);
      scratch.select();
      const done = document.execCommand('copy');
      document.body.removeChild(scratch);
      if (!done) throw new Error('copia manual');
    }
    mark('Copiado', 'ok');
  } catch (_error) {
    // Último recurso honesto: se selecciona el texto de la fila para
    // que el atajo de copia del navegador funcione, y el botón lo
    // dice en vez de fingir que copió.
    if (button && button.parentElement && window.getSelection) {
      const range = document.createRange();
      range.selectNodeContents(button.parentElement);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    }
    mark('Selecciona y copia', 'warn');
  }
}

// ------------------------ Selector global ------------------------

/** Opciones del <select>. El LED del estado va al lado, no dentro. */
function renderHostSelector() {
  const select = $('host-select');
  if (!select) return;
  const previous = String(state.hostId);
  select.innerHTML = state.hosts
    .map((host) => {
      const isLocal = Number(host.is_local) === 1;
      const suffix = isLocal ? '' : ` · agente #${host.id}`;
      return `<option value="${escapeHtml(String(host.id))}">${escapeHtml(host.name)}${suffix}</option>`;
    })
    .join('');
  if (previous) select.value = previous;
  const group = select.parentElement;
  if (group) group.dataset.scope = isRemoteScope() ? 'remote' : 'local';
  renderHostSelectorLed();
}

/** LED del selector: el estado del host elegido, con la misma paleta. */
function renderHostSelectorLed() {
  const host = state.host;
  const led = $('host-select-led');
  if (led) led.className = `nx-led ${host ? hostLedClass(host) : 'nx-led--idle'}`;
  const scopeLed = $('scope-led');
  if (scopeLed) scopeLed.className = `nx-led ${host ? hostLedClass(host) : 'nx-led--idle'}`;
}

/**
 * Veredicto de la franja: con un host remoto, el chip de ámbito
 * nombra al agente para que ningún contador de la fila se lea como
 * si fuera de este servidor.
 */
function renderHostScope() {
  const host = state.host;
  const chip = $('scope-chip');
  const name = $('scope-name');
  const remote = isRemoteScope();
  if (name) name.textContent = host ? host.name : 'Este servidor';
  if (chip) chip.dataset.scope = remote ? 'remote' : 'local';
  renderHostSelectorLed();

  // El drilledown de los contadores de severidad salta al registro de
  // alarmas, que es de SESIÓN y por tanto del host local. Con un host
  // remoto marcado los chips dejan de ser botones: un contador que no
  // lleva a sus propias alarmas no debe parecer que lleva.
  document.querySelectorAll('.nx-count[data-severity]').forEach((chipNode) => {
    chipNode.disabled = remote;
    chipNode.setAttribute('aria-disabled', String(remote));
    chipNode.title = remote
      ? 'El registro de alarmas de la sesión es del servidor local.'
      : '';
  });
}

/** Persiste la elección y la aplica a todo lo que depende de ella. */
function selectHost(hostId, options = {}) {
  const id = Number(hostId);
  const known = state.hosts.some((host) => Number(host.id) === id);
  state.hostId = known ? id : 1;
  state.host = selectedHost();
  if (options.persist !== false) {
    try {
      window.localStorage.setItem(HOST_STORAGE_KEY, String(state.hostId));
    } catch (_error) {
      // Modo privado o almacenamiento lleno: la selección vive solo en
      // esta pestaña, que es un comportamiento aceptable.
    }
  }
  const select = $('host-select');
  if (select) select.value = String(state.hostId);
  renderHostScope();
  applyHostScope();
}

/** Lee la elección guardada. Sin invalidarla: la resuelve el inventario. */
function readStoredHostId() {
  try {
    const raw = window.localStorage.getItem(HOST_STORAGE_KEY);
    const id = Number(raw);
    return Number.isInteger(id) && id > 0 ? id : null;
  } catch (_error) {
    return null;
  }
}

/**
 * Todo lo que cambia al cambiar de host. Se mantiene en un sitio
 * porque los olvidos se pagan: un selector que repinta el Resumen y
 * no el Histórico es un panel con dos verdades.
 */
async function applyHostScope() {
  const remote = isRemoteScope();

  // 1. Avisos de sección local.
  updateLocalOnlyNotices();

  // 2. Franja de veredicto.
  renderRollup();

  // 3. Resumen: banda de sensores.
  if (remote) {
    await renderRemoteSensors();
  } else {
    // Se repinta la última lectura local: el muestreo de 5 s nunca se
    // paró, así que no hace falta pedir nada al volver.
    restoreLocalScope();
    if (state.localSnapshot) paintLocalMetrics(state.localSnapshot);
  }

  // 4. Histórico: se invalida la caché de la pestaña y se vuelve a
  //    pedir con el host nuevo. Si la pestaña está abierta, se pinta
  //    al instante; si no, se pintará al abrirla.
  state.loaded.history = false;
  const historySection = $('section-history');
  const historyVisible = historySection && !historySection.classList.contains('hidden');
  if (historyVisible) {
    state.loaded.history = true;
    fetchHistory();
  } else {
    updateHistoryCsvNote();
  }

  // 5. Pestañas dependientes del host: se refresca la que esté abierta.
  //    El sondeo de 5 s no manda comandos al agente; estas lecturas son
  //    bajo demanda (abrir pestaña o cambiar de host).
  closeContainerDetail();
  const serviceDetail = $('service-detail');
  if (serviceDetail) serviceDetail.classList.add('hidden');
  if (isTabVisible('containers')) {
    fetchDockerInventory();
    fetchContainers();
  }
  if (isTabVisible('services')) {
    fetchServices();
    fetchInstallCatalog();
  }
  if (isTabVisible('network')) fetchNetworkState();
  if (isTabVisible('system')) {
    fetchPorts();
    fetchUpdates();
    fetchSmart();
  }

  renderHostSummary();
}

/**
 * Avisos de lo que sigue siendo exclusivo del servidor del panel. Las
 * pestañas de contenedores y servicios ya hablan con el agente del host
 * elegido, así que sus avisos desaparecen; en Red solo queda la
 * configuración, que sigue siendo local.
 */
function updateLocalOnlyNotices() {
  const remote = isRemoteScope();
  ['localonly-containers', 'localonly-services'].forEach((id) => {
    const node = $(id);
    if (!node) return;
    node.textContent = '';
    node.classList.add('hidden');
  });
  const network = $('localonly-network');
  if (network) {
    network.textContent = remote
      ? 'Inventario y escaneo se leen del host remoto. La configuración de red solo se aplica en el host local.'
      : '';
    network.classList.toggle('hidden', !remote);
  }
}

// ------------------- Resumen de un host remoto --------------------

/**
 * La banda de sensores con datos de agente. Se pinta en los MISMOS
 * ids que usa el host local (`metric-*`, `bar-*`, `led-*`, `state-*`,
 * `fresh-*`, sparklines): no hay una segunda banda de sensores que
 * mantener ni dos vocabularios de estado en la misma tabla.
 *
 * El punto de partida es la última muestra de la serie de 1 h. La
 * frescura que se muestra es la del propio punto —no la del momento
 * en que se pintó— porque en un agregado de 1 m "ahora" mentiría.
 */
async function renderRemoteSensors() {
  const host = state.host;
  if (!host) return;
  const note = $('sensor-scope-note');
  const dashboardNote = $('dashboard-scope-note');
  try {
    const payload = await apiFetch(
      `/api/hosts/${encodeURIComponent(String(host.id))}/metrics/history?range=1h&metric=${REMOTE_METRICS}`
    );
    applyRemoteSensorBand(payload);
  } catch (error) {
    console.error('Error obteniendo métricas del host remoto:', error.message);
    state.remoteSensorStatus = {};
    SENSORS.forEach((sensor) => {
      const led = $(`led-${sensor.key}`);
      if (led) led.className = 'nx-led nx-led--unknown';
      const cell = $(`state-${sensor.key}`);
      if (cell) {
        cell.textContent = SENSOR_STATE_LABEL.unknown;
        cell.className = 'nx-sr-st nx-sr-st--unknown';
      }
    });
    if (note) note.textContent = `Sin datos del agente: ${error.message}`;
    if (dashboardNote) {
      dashboardNote.textContent = `No se pudo leer el histórico de «${host.name}»: ${error.message}`;
      dashboardNote.classList.remove('hidden');
    }
    renderRollup();
  }
}

/**
 * Escribe la muestra remota en la banda. Duplica un trozo de
 * `updateSensor` a propósito: allí el estado va acompañado de alarma
 * de sesión y de push a la ventana en memoria, y ninguna de las dos
 * cosas es cierta para un host remoto cuyo registro de alarmas es del
  servidor local. Aquí se pinta el estado y el minigráfico, y nada
 * más.
 */
function applyRemoteSensorBand(payload) {
  const host = state.host;
  if (!host || !payload) return;

  const series = payload.series || {};
  const points = (key) => (series[key] || []).filter((point) => Number.isFinite(point.value));
  // La clave del sensor `load` es `load` en el panel y `load1` en el
  // histórico: se traduce una vez, aquí, y no en el marcado.
  const metricKey = { load: 'load1' };
  const lastOf = (key) => {
    const list = points(metricKey[key] || key);
    return list.length ? list[list.length - 1] : null;
  };

  state.remoteSensorStatus = {};
  state.remoteSeries = {};
  let newest = 0;

  SENSORS.forEach((sensor) => {
    const key = sensor.key;
    const last = lastOf(key);
    const values = points(metricKey[key] || key).map((point) => Number(point.value));
    state.remoteSeries[key] = values;

    // La carga del agente es un valor ABSOLUTO y no dice cuántos
    // núcleos tiene la máquina, así que el umbral por núcleo del
    // panel no se le puede aplicar sin inventarse el dato: se pinta
    // el número y la fila queda en gris ("sin dato"), que es lo que
    // significa en este sistema. Valores fuera de escala se
    // recortean en lugar de romper el minigráfico.
    const unjudgeable = key === 'load';
    const value = unjudgeable || !last ? null : Number(last.value);
    const status = unjudgeable ? 'unknown' : sensorStatus(sensor, value);
    if (last) newest = Math.max(newest, Number(last.ts) * 1000);

    const led = $(`led-${key}`);
    if (led) led.className = `nx-led ${LED_CLASS[status]}`;
    const row = document.querySelector(`.nx-sensor[data-sensor="${key}"]`);
    if (row) row.dataset.status = status;
    const stateCell = $(`state-${key}`);
    if (stateCell) {
      stateCell.textContent = unjudgeable ? 'Sin normalizar' : (SENSOR_STATE_LABEL[status] || SENSOR_STATE_LABEL.unknown);
      stateCell.className = `nx-sr-st nx-sr-st--${status}`;
    }

    const metricCell = $(`metric-${key}`);
    if (metricCell) {
      metricCell.textContent = last ? Number(last.value).toFixed(sensor.digits) : '--';
    }
    updateBar(`bar-${key}`, last && !unjudgeable ? (Number(last.value) / sensor.crit) * 100 : 0, status);
    drawSpark(sensor, values);
    if (unjudgeable) {
      state.remoteSensorStatus[key] = 'unknown';
    } else {
      state.remoteSensorStatus[key] = status;
    }
  });

  // Frescura: la del punto, no la del pintado.
  state.remoteFreshAt = newest;
  SENSORS.forEach((sensor) => {
    const fresh = $(`fresh-${sensor.key}`);
    if (fresh) fresh.textContent = newest ? formatRelative(newest) : '—';
  });

  // El aviso de carga deja de anunciar un umbral que aquí no aplica.
  const loadNote = $('metric-load-note');
  if (loadNote) {
    loadNote.textContent = 'Valor absoluto del agente · sin dato de núcleos, umbral por núcleo no aplicable';
  }
  const tempNote = $('metric-temp-note');
  if (tempNote) {
    const hasTemp = state.remoteSeries.temp && state.remoteSeries.temp.length > 0;
    tempNote.textContent = hasTemp ? 'Aviso 70 °C · crítico 80 °C' : 'Sin sensor en este host';
  }

  // Nota de la cabecera del panel de sensores: de dónde viene la fila.
  const note = $('sensor-scope-note');
  if (note) {
    const age = newest ? formatRelative(newest) : 'sin muestras';
    note.textContent = `datos del agente · ${age} · cada ${payload.period || '1m'}`;
  }
  const dashboardNote = $('dashboard-scope-note');
  if (dashboardNote) {
    dashboardNote.textContent = `Estás viendo el resumen del AGENTE de «${host.name}». Los seis sensores vienen de su latido; ` +
      'los contenedores, la red, los servicios y el bloque de datos de hardware son de este servidor.';
    dashboardNote.classList.remove('hidden');
  }
  renderRollup();
  renderFreshness();
}

/**
 * Vuelve a pintar la banda como local. Sustituye a `renderMetrics`
 * para el camino del selector, y deja los rótulos de umbral con su
 * texto de siempre (el de la carga vuelve a anunciar núcleos).
 */
function restoreLocalScope() {
  const loadNote = $('metric-load-note');
  const tempNote = $('metric-temp-note');
  const note = $('sensor-scope-note');
  const dashboardNote = $('dashboard-scope-note');
  if (note) note.textContent = 'Lectura local · 5 s';
  if (dashboardNote) {
    dashboardNote.textContent = '';
    dashboardNote.classList.add('hidden');
  }
  if (loadNote && state.localSnapshot) {
    const loadAvg = Array.isArray(state.localSnapshot.load_avg) ? state.localSnapshot.load_avg : [];
    const cores = Math.max(1, Number(state.localSnapshot.cpu_count) || 1);
    const loadRaw = Number(loadAvg[0]);
    loadNote.textContent = Number.isFinite(loadRaw)
      ? `Aviso 0.70 · crítico 1.00 · ${cores} ${cores === 1 ? 'núcleo' : 'núcleos'}`
      : 'Carga no disponible';
  }
  if (tempNote && state.localSnapshot) {
    const temp = state.localSnapshot.cpu_temp_c;
    tempNote.textContent = (temp === null || temp === undefined || !Number.isFinite(Number(temp)))
      ? 'Sin sensor en este host'
      : 'Aviso 70 °C · crítico 80 °C';
  }
  state.remoteSensorStatus = {};
  state.remoteFreshAt = 0;
  state.remoteSeries = {};
}

// ============================ Navegación ============================
/**
 * Cambio de pestaña. Cada sección se pide la PRIMERA vez que se
 * abre y se recuerda en `state.loaded`: volver a la pestaña de
 * alertas no tiene que reventar cinco peticiones cada vez, igual
 * que volver al Resumen no vuelve a pedir los contenedores aparte
 * del sondeo. Un botón de refresco explícito siempre está.
 */
const TAB_LOADERS = {
  // La vista por defecto de cada riel la carga showSubtab(); aquí
  // sólo va lo que la vista inicial no cubre (los eventos que
  // alimentan la severidad de las reglas, el inventario Docker).
  alerts: ensureAlertEvents,
  history: fetchHistory,
  monitors: fetchMonitors,
  audit: fetchAudit,
  system: () => { fetchPorts(); fetchUpdates(); fetchSmart(); },
  containers: fetchDockerInventory
};

/**
 * Carga la pestaña de Hosts. El inventario alimenta también al
 * selector de la cabecera, así que se pide una sola vez y se
 * reutiliza: abrir la pestaña no debe ser la primera vez que se ve
 * la flota.
 */
function loadHostsTab() {
  renderHosts();
  renderHostSelector();
  renderHostScope();
  updateLocalOnlyNotices();
  fetchHosts();
  fetchEnrollment();
}

function showTab(name) {
  document.body.dataset.tab = name;
  document.querySelectorAll('.tab-section').forEach((section) => section.classList.add('hidden'));

  if (name === 'chat') {
    const chatPanel = $('chat-panel');
    if (chatPanel) chatPanel.classList.remove('hidden');
  } else {
    const section = $(`section-${name}`);
    if (section) section.classList.remove('hidden');
    // Al abrir la pestaña de servicios se refresca el inventario.
    if (name === 'services') {
      fetchServices();
      fetchInstallCatalog();
    }
    // Al abrir la pestaña de red se refresca el estado de red.
    if (name === 'network') fetchNetworkState();
    // Contenedores remotos: se piden al abrir, no en el sondeo de 5 s.
    if (name === 'containers' && isRemoteScope()) fetchContainers();
    // Sistema remoto: cada apertura vuelve a preguntar al agente.
    if (name === 'system' && isRemoteScope()) {
      state.loaded.system = true;
      fetchPorts();
      fetchUpdates();
      fetchSmart();
    }
    // La flota se refresca al abrirla: el estado de un host cambia
    // por su cuenta (latido, habilitación) y una lista de hosts
    // vieja es peor que no tenerla. El token de enrolamiento también
    // se relee porque puede haber rotado.
    if (name === 'hosts') loadHostsTab();
    // Las pestañas nuevas se cargan una vez. "security" tiene además
    // un riel interno: Tokens es su vista inicial.
    if (name === 'security') showSubtab('security', 'tokens');
    if (name === 'alerts') showSubtab('alerts', 'rules');
    const loader = TAB_LOADERS[name];
    if (loader && !state.loaded[name]) {
      state.loaded[name] = true;
      loader();
    }
    // El panel de chat permanece visible solo en pantallas grandes (lg:block).
    const chatPanel = $('chat-panel');
    if (chatPanel) chatPanel.classList.add('hidden');
  }

  document.querySelectorAll('.tab-btn').forEach((button) => {
    button.classList.toggle('tab-active', button.dataset.tab === name);
  });

  window.dispatchEvent(new CustomEvent('nx-tab', { detail: { name } }));
}

// ============================== Gráfica ==============================
// Paleta compartida con styles.css (grafito + azul corporativo). El
// relleno es un degradado que se construye sobre el propio canvas:
// Chart.js no admite degradados en CSS, así que se generan en el momento
// en que el lienzo ya tiene tamaño real. La serie RAM conserva su
// violeta: es la única identidad cromática que no es ni estado ni azul
// de marca.
const CHART_COLORS = {
  ink: '#e6edf3',
  inkMuted: '#8b949e',
  inkQuiet: '#7d8994',
  surface: '#161b22',
  border: '#30363d',
  cpu: '#2f81f7',
  ram: '#9d8cff',
  mono: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'
};

/** Relleno vertical suave para cada serie, anclado abajo. */
function chartAreaFill(hex, topAlpha) {
  return (context) => {
    const { chart } = context;
    const { ctx, chartArea } = chart;
    if (!chartArea) return 'transparent';
    const gradient = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
    gradient.addColorStop(0, hexToRgba(hex, topAlpha));
    gradient.addColorStop(1, hexToRgba(hex, 0));
    return gradient;
  };
}

function hexToRgba(hex, alpha) {
  const value = hex.replace('#', '');
  const int = parseInt(value, 16);
  const r = (int >> 16) & 255;
  const g = (int >> 8) & 255;
  const b = int & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

// Puntos ocultos salvo el primero: hasta que la ventana se llena, un
// único punto visible evita que la gráfica arranque como una cuadrícula
// vacía durante los primeros segundos.
const pointRadius = (ctx) => (ctx.dataset.data.length <= 1 ? 3 : 0);

function initChart() {
  const canvas = $('resource-chart');
  if (!canvas || typeof Chart === 'undefined') return;

  Chart.defaults.font.family = CHART_COLORS.mono;
  Chart.defaults.font.size = 11;

  state.chart = new Chart(canvas, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        {
          label: 'CPU',
          data: [],
          borderColor: CHART_COLORS.cpu,
          backgroundColor: chartAreaFill(CHART_COLORS.cpu, 0.12),
          fill: true,
          tension: 0.3,
          borderWidth: 1.5,
          borderJoinStyle: 'round',
          pointRadius,
          pointHoverRadius: 4,
          pointHoverBorderWidth: 2,
          pointHoverBorderColor: CHART_COLORS.surface,
          pointHoverBackgroundColor: CHART_COLORS.cpu
        },
        {
          label: 'RAM',
          data: [],
          borderColor: CHART_COLORS.ram,
          backgroundColor: chartAreaFill(CHART_COLORS.ram, 0.08),
          fill: true,
          tension: 0.3,
          borderWidth: 1.5,
          borderJoinStyle: 'round',
          pointRadius,
          pointHoverRadius: 4,
          pointHoverBorderWidth: 2,
          pointHoverBorderColor: CHART_COLORS.surface,
          pointHoverBackgroundColor: CHART_COLORS.ram
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      normalized: true,
      interaction: { intersect: false, mode: 'index' },
      layout: { padding: { top: 4, right: 4 } },
      scales: {
        y: {
          min: 0,
          max: 100,
          border: { display: false },
          ticks: {
            color: CHART_COLORS.inkQuiet,
            stepSize: 25,
            padding: 8,
            callback: (value) => `${value}%`
          },
          // Solo rejilla horizontal, punteada y muy tenue: la referencia
          // son los valores, no la cuadrícula.
          grid: {
            color: hexToRgba(CHART_COLORS.ink, 0.07),
            borderDash: [3, 4],
            drawTicks: false
          }
        },
        x: {
          border: { display: false },
          ticks: {
            color: CHART_COLORS.inkQuiet,
            maxTicksLimit: 6,
            maxRotation: 0,
            padding: 8
          },
          grid: { display: false }
        }
      },
      plugins: {
        // La leyenda la pone la cabecera del panel (chips propios).
        legend: { display: false },
        tooltip: {
          backgroundColor: CHART_COLORS.surface,
          titleColor: CHART_COLORS.inkQuiet,
          bodyColor: CHART_COLORS.ink,
          borderColor: CHART_COLORS.border,
          borderWidth: 1,
          cornerRadius: 6,
          padding: 10,
          boxWidth: 8,
          boxHeight: 8,
          boxPadding: 4,
          usePointStyle: true,
          titleFont: { family: CHART_COLORS.mono, size: 11 },
          bodyFont: { family: CHART_COLORS.mono, size: 12 },
          callbacks: { label: (ctx) => `${ctx.dataset.label}  ${ctx.parsed.y}%` }
        }
      }
    }
  });
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

  // Umbral 0 y raíz extendida 8 % POR DEBAJO del viewport.
  // Las dos decisiones vienen del mismo fallo: con rootMargin negativo
  // ("0px 0px -8% 0px") los últimos 72px de cualquier página quedan
  // fuera de la raíz de observación, así que el último bloque —el riel
  // de hechos del Resumen— nunca llega a dispararse y se queda en
  // opacidad 0 para siempre. Contenido que no aparece no es una
  // animación, está escondido. Al extender la raíz hacia abajo el
  // fundido arranca justo cuando el bloque se acerca (que es lo que
  // quería el margen negativo) y además el final de la página siempre
  // queda dentro.
  //
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
    { threshold: 0, rootMargin: '0px 0px 8% 0px' }
  );

  // Las secciones de pestaña empiezan en display:none: nunca se solapan
  // con el observador, así que se revelan solas cuando la pestaña las
  // muestra. No hace falta observarlas de nuevo.
  items.forEach((item) => {
    observer.observe(item);
  });
}

// ============================ Arranque ============================
/**
 * Sondeo automático. La cadencia NO cambia: sigue siendo la misma de
 * CONFIG.POLL_INTERVAL_MS. Lo único que se añade son dos puertas que
 * lo saltan —la pestaña en segundo plano, que ya estaba, y ahora la
 * pausa manual del usuario—. Ninguna de las dos cancela el
 * temporizador, así que reanudar es inmediato y no reconstruye nada.
 */
function startPolling() {
  if (state.pollTimer) window.clearInterval(state.pollTimer);
  state.nextPollAt = Date.now() + CONFIG.POLL_INTERVAL_MS;
  state.pollTimer = window.setInterval(() => {
    // Se pausa el sondeo cuando la pestaña no está visible (ahorro en la Pi).
    if (document.visibilityState !== 'visible') return;
    if (state.pollPaused) return;
    state.nextPollAt = Date.now() + CONFIG.POLL_INTERVAL_MS;
    fetchMetrics();
    if (!isRemoteScope()) fetchContainers();
  }, CONFIG.POLL_INTERVAL_MS);
}

/* ------------------------- Terminal SSH ------------------------- */
let terminalSession = null;
let terminalResizeObserver = null;

function disconnectTerminalResize() {
  if (!terminalResizeObserver) return;
  terminalResizeObserver.disconnect();
  terminalResizeObserver = null;
}

function setTerminalStatus(text, isError = false) {
  const node = $('terminal-status');
  if (!node) return;
  node.textContent = text;
  node.classList.toggle('nx-status--error', Boolean(isError));
}

function sendTerminalResize(session) {
  if (!session || session.ws.readyState !== WebSocket.OPEN) return;
  session.ws.send(JSON.stringify({ type: 'resize', cols: session.term.cols, rows: session.term.rows }));
}

function sizeTerminalSection() {
  const section = $('section-terminal');
  if (!section || document.body.dataset.tab !== 'terminal') return;
  const top = Math.max(0, section.getBoundingClientRect().top);
  section.style.height = `${Math.max(320, Math.round(window.innerHeight - top - 16))}px`;
}

async function fetchTerminalTargets() {
  const select = $('terminal-host');
  try {
    const payload = await apiFetch('/api/terminal/targets');
    state.terminalTargets = Array.isArray(payload.targets) ? payload.targets : [];
  } catch (error) {
    state.terminalTargets = [];
    state.terminalHostId = null;
    if (select) {
      select.innerHTML = '<option value="">Sin conexión con el hub</option>';
      select.disabled = true;
    }
    setTerminalStatus(`No se pudo cargar la lista de servidores: ${error.message}`, true);
    return false;
  }

  const available = state.terminalTargets.filter((target) => target.available);
  const saved = Number(window.localStorage.getItem('nexus.terminalHost') || 0);
  const selected = available.find((target) => Number(target.id) === saved)
    || available.find((target) => target.is_local)
    || available[0]
    || null;
  state.terminalHostId = selected ? Number(selected.id) : null;

  if (!select) return Boolean(selected);
  if (!state.terminalTargets.length) {
    select.innerHTML = '<option value="">Sin servidores conectados</option>';
    select.disabled = true;
    setTerminalStatus('No hay servidores conectados con terminal disponible.', true);
    return false;
  }

  select.innerHTML = state.terminalTargets
    .map((target) => {
      const label = `${target.name}${target.is_local ? ' (este servidor)' : ''}${
        target.available ? '' : ` — ${target.reason || 'sin SSH'}`
      }`;
      return `<option value="${escapeHtml(String(target.id))}"${
        target.available ? '' : ' disabled'
      }>${escapeHtml(label)}</option>`;
    })
    .join('');
  select.disabled = available.length === 0;
  if (selected) select.value = String(selected.id);
  if (!selected) {
    setTerminalStatus('No hay servidores con terminal disponible.', true);
    return false;
  }
  return true;
}

async function prepareTerminalSession() {
  setTerminalStatus('Cargando servidores…');
  const ready = await fetchTerminalTargets();
  if (document.body.dataset.tab !== 'terminal') return;
  sizeTerminalSection();
  closeTerminalSession();
  if (ready) openTerminalSession();
}

function closeTerminalSession() {
  const session = terminalSession;
  terminalSession = null;
  disconnectTerminalResize();
  if (!session) return;
  try { session.ws.close(1000); } catch (_error) { /* ya cerrado */ }
  try { session.term.dispose(); } catch (_error) { /* ya liberado */ }
  try { session.host.innerHTML = ''; } catch (_error) { /* sin host */ }
}

function openTerminalSession() {
  sizeTerminalSection();
  const host = $('terminal');
  if (!host) return;
  const target = state.terminalTargets.find(
    (item) => Number(item.id) === Number(state.terminalHostId)
  );
  if (!target || !target.available) {
    setTerminalStatus(target?.reason || 'Selecciona un servidor con terminal disponible.', true);
    return;
  }
  if (terminalSession) {
    if (Number(terminalSession.hostId) === Number(target.id)) {
      try { terminalSession.fit.fit(); terminalSession.term.focus(); } catch (_error) { /* oculto */ }
      return;
    }
    closeTerminalSession();
  }
  if (!window.Terminal || !window.FitAddon) {
    setTerminalStatus('El emulador de terminal no está disponible.', true);
    return;
  }

  const term = new window.Terminal({
    cursorBlink: true,
    convertEol: true,
    fontFamily: '"Cascadia Mono", Consolas, monospace',
    fontSize: 13,
    scrollback: 2000,
    theme: {
      background: '#080d16',
      foreground: '#d7e3ef',
      cursor: '#4da3ff',
      selectionBackground: 'rgba(77, 163, 255, 0.25)',
    },
  });
  const fit = new window.FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(host);
  try { fit.fit(); } catch (_error) { /* aún sin tamaño */ }

  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(
    `${scheme}://${window.location.host}/api/terminal/ws?host=${encodeURIComponent(String(target.id))}`,
    ['nexus-terminal', `csrf.${state.csrf || ''}`]
  );
  ws.binaryType = 'arraybuffer';
  const session = { term, fit, ws, host, hostId: Number(target.id) };
  terminalSession = session;

  // El panel cambia de ancho al ocultar el Asistente y al redimensionar
  // la ventana. ResizeObserver ajusta xterm sin esperar a un resize global.
  if (window.ResizeObserver) {
    disconnectTerminalResize();
    terminalResizeObserver = new ResizeObserver(() => {
      if (!terminalSession) return;
      try { terminalSession.fit.fit(); } catch (_error) { /* oculto */ }
      sendTerminalResize(terminalSession);
    });
    terminalResizeObserver.observe(host);
  }

  ws.onopen = () => {
    setTerminalStatus(`Conectado a ${target.name} por SSH.`);
    try { fit.fit(); } catch (_error) { /* sin tamaño */ }
    sendTerminalResize(session);
    term.focus();
  };
  ws.onmessage = (event) => {
    const data = typeof event.data === 'string' ? event.data : new Uint8Array(event.data);
    term.write(data);
  };
  ws.onerror = () => setTerminalStatus('Error de conexión con el terminal.', true);
  ws.onclose = (event) => {
    setTerminalStatus(
      event.code === 1000 ? 'Sesión cerrada.' : `Sesión cerrada (${event.code}).`,
      event.code !== 1000
    );
    if (terminalSession === session) {
      terminalSession = null;
      disconnectTerminalResize();
      try { term.dispose(); } catch (_error) { /* ya liberado */ }
      try { host.innerHTML = ''; } catch (_error) { /* sin host */ }
    }
  };
  term.onData((data) => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'input', data }));
    }
  });
  term.onResize(() => sendTerminalResize(session));
}

window.addEventListener('nx-tab', (event) => {
  if (event.detail && event.detail.name === 'terminal') {
    window.setTimeout(prepareTerminalSession, 120);
  } else {
    closeTerminalSession();
  }
});

const terminalHostSelect = $('terminal-host');
if (terminalHostSelect) {
  terminalHostSelect.addEventListener('change', () => {
    const target = state.terminalTargets.find(
      (item) => Number(item.id) === Number(terminalHostSelect.value)
    );
    if (!target || !target.available) {
      setTerminalStatus(target?.reason || 'Servidor sin terminal disponible.', true);
      return;
    }
    state.terminalHostId = Number(target.id);
    try {
      window.localStorage.setItem('nexus.terminalHost', String(target.id));
    } catch (_error) { /* almacenamiento no disponible */ }
    closeTerminalSession();
    setTerminalStatus(`Conectando a ${target.name}…`);
    window.setTimeout(openTerminalSession, 120);
  });
}

const terminalReconnect = $('terminal-reconnect');
if (terminalReconnect) {
  terminalReconnect.addEventListener('click', () => {
    closeTerminalSession();
    prepareTerminalSession();
  });
}

window.addEventListener('resize', () => {
  sizeTerminalSection();
  if (!terminalSession) return;
  try { terminalSession.fit.fit(); } catch (_error) { /* oculto */ }
  sendTerminalResize(terminalSession);
});

function registerEventListeners() {
  document.querySelectorAll('.tab-btn').forEach((button) => {
    button.addEventListener('click', () => showTab(button.dataset.tab));
  });

  // ------------------------- Flota de hosts -------------------------
  // Selector global: el cambio se guarda en localStorage y lo aplica
  // `selectHost`, que es el único camino por el que se repinta el
  // Resumen y el Histórico.
  const hostSelect = $('host-select');
  if (hostSelect) {
    hostSelect.addEventListener('change', () => selectHost(hostSelect.value));
  }

  const hostsTable = $('hosts-table');
  if (hostsTable) {
    hostsTable.addEventListener('click', (event) => {
      const edit = event.target.closest('[data-hosts-edit]');
      if (edit) {
        openHostEditor(edit.dataset.hostsEdit);
        return;
      }
      const remove = event.target.closest('[data-hosts-delete]');
      if (remove) deleteHost(remove.dataset.hostsDelete, remove.dataset.hostsName || '');
    });
  }

  const hostsForm = $('hosts-edit-form');
  if (hostsForm) hostsForm.addEventListener('submit', saveHost);
  const hostsCancel = $('hosts-edit-cancel');
  if (hostsCancel) hostsCancel.addEventListener('click', closeHostEditor);

  const hostsRefresh = $('hosts-refresh');
  if (hostsRefresh) {
    hostsRefresh.addEventListener('click', () => {
      fetchHosts();
      fetchEnrollment();
    });
  }

  const copyToken = $('hosts-copy-token');
  if (copyToken) {
    copyToken.addEventListener('click', () => {
      const node = $('hosts-enroll-token');
      copyWithFeedback(node ? node.textContent.trim() : '', copyToken, 'Copiar token');
    });
  }
  document.querySelectorAll('[data-hosts-copy]').forEach((button) => {
    button.addEventListener('click', () => {
      const source = {
        enroll: 'hosts-enroll-command',
        run: 'hosts-run-command',
        install: 'hosts-install-command'
      }[button.dataset.hostsCopy];
      const node = source ? $(source) : null;
      if (node) copyWithFeedback(node.textContent.trim(), button, 'Copiar');
    });
  });

  // Sub-pestañas: un solo contrato para los dos rieles (alertas y
  // seguridad). El cargador de la vista lo pone showSubtab().
  document.querySelectorAll('.nx-subtab').forEach((button) => {
    button.addEventListener('click', () => showSubtab(button.dataset.subgroup, button.dataset.subtab));
  });

  // Barra de sondeo: pausar y reanudar, con la cuenta al lado.
  const pollToggle = $('poll-toggle');
  if (pollToggle) {
    pollToggle.addEventListener('click', () => setPollingPaused(!state.pollPaused));
  }

  // Filtros de alarma (dentro del panel).
  document.querySelectorAll('[data-alarm-filter]').forEach((button) => {
    button.addEventListener('click', () => applyAlarmFilter(button.dataset.alarmFilter, false));
  });

  // Chips de severidad: drilling-down. saltan al panel con el filtro puesto.
  document.querySelectorAll('.nx-count[data-severity]').forEach((chip) => {
    chip.addEventListener('click', () => applyAlarmFilter(chip.dataset.severity, true));
  });

  $('deploy-form').addEventListener('submit', deployContainer);

  $('refresh-containers').addEventListener('click', fetchContainers);
  // Refresco a mano de la lista de sensores (mismo camino que el
  // sondeo automático; no añade ninguna llamada nueva al backend).
  const refreshMetrics = $('refresh-metrics');
  if (refreshMetrics) refreshMetrics.addEventListener('click', fetchMetrics);
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

  registerAlertsListeners();
  registerHistoryListeners();
  registerMonitorsListeners();
  registerAuditListeners();
  registerSystemListeners();
  registerSecurityListeners();
  registerContainerListeners();

  // Al volver a la pestaña, refresca de inmediato.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      fetchMetrics();
      if (!isRemoteScope()) fetchContainers();
    }
  });
}

// ------------------- Alturas de las vistas nuevas -------------------
// Los escuchadores viven aquí, al final, y no repartidos por el
// fichero: un bloque por pestaña, en el mismo orden en que están las
// pestañas del riel. Todos toleran que el elemento no exista —cada
// `if`— para que el mismo inicializador valga para cualquier
// subconjunto del HTML.

function registerAlertsListeners() {
  const on = (id, event, handler) => {
    const node = $(id);
    if (node) node.addEventListener(event, handler);
  };

  // Reglas
  on('alerts-rules-new', 'click', () => openRuleForm(null));
  on('refresh-alert-rules', 'click', fetchAlertRules);
  on('rule-cancel', 'click', closeRuleForm);
  $('alert-rule-form')?.addEventListener('submit', withBusy('rule-save', 'Guardando', submitRule));

  // Eventos
  on('refresh-alert-events', 'click', fetchAlertEvents);
  document.querySelectorAll('[data-event-filter]').forEach((button) => {
    button.addEventListener('click', () => {
      state.eventFilter = button.dataset.eventFilter;
      document.querySelectorAll('[data-event-filter]').forEach((other) => {
        other.setAttribute('aria-pressed', String(other.dataset.eventFilter === state.eventFilter));
      });
      fetchAlertEvents();
    });
  });

  // Canales
  on('channel-new', 'click', () => openChannelForm(null));
  on('refresh-channels', 'click', fetchChannels);
  on('channel-cancel', 'click', closeChannelForm);
  on('channel-kind', 'change', applyChannelKindFields);
  $('channel-form')?.addEventListener('submit', withBusy('channel-save', 'Guardando', submitChannel));

  // Mantenimiento
  on('mw-new', 'click', () => openWindowForm(null));
  on('refresh-mw', 'click', fetchWindows);
  on('mw-cancel', 'click', closeWindowForm);
  $('mw-form')?.addEventListener('submit', withBusy('mw-save', 'Guardando', submitWindow));

  // Propuestas
  on('refresh-proposals', 'click', fetchProposals);
}

function registerHistoryListeners() {
  document.querySelectorAll('[data-range]').forEach((button) => {
    button.addEventListener('click', () => setHistoryRange(button.dataset.range));
  });
  document.querySelectorAll('[data-pick]').forEach((button) => {
    button.addEventListener('click', () => toggleHistoryMetric(button.dataset.pick));
  });
  const refresh = $('refresh-history');
  if (refresh) refresh.addEventListener('click', fetchHistory);
}

function registerMonitorsListeners() {
  const on = (id, event, handler) => {
    const node = $(id);
    if (node) node.addEventListener(event, handler);
  };
  on('monitor-new', 'click', () => openMonitorForm(null));
  on('refresh-monitors', 'click', fetchMonitors);
  on('monitor-cancel', 'click', closeMonitorForm);
  on('mon-kind', 'change', applyMonitorKindFields);
  on('monitor-detail-close', 'click', closeMonitorDetail);
  $('monitor-form')?.addEventListener('submit', withBusy('mon-save', 'Guardando', submitMonitor));

  document.querySelectorAll('[data-hb-hours]').forEach((button) => {
    button.addEventListener('click', () => setHeartbeatHours(Number(button.dataset.hbHours)));
  });
}

function registerAuditListeners() {
  const on = (id, event, handler) => {
    const node = $(id);
    if (node) node.addEventListener(event, handler);
  };
  on('refresh-audit', 'click', fetchAudit);
  on('audit-prev', 'click', () => {
    state.audit.offset = Math.max(0, state.audit.offset - state.audit.limit);
    fetchAudit();
  });
  on('audit-next', 'click', () => {
    state.audit.offset += state.audit.limit;
    fetchAudit();
  });
  on('audit-limit', 'change', () => {
    state.audit.limit = Number($('audit-limit').value) || 50;
    resetAuditAndFetch();
  });

  // Los filtros de texto llegan mientras se escribe: 350 ms de
  // espera para no enviar una petición por pulsación, y vuelta a la
  // primera página porque el offset anterior puede quedar fuera.
  let timer = null;
  const onFilter = (id, apply) => {
    const node = $(id);
    if (!node) return;
    node.addEventListener('input', () => {
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        apply(node.value.trim());
        resetAuditAndFetch();
      }, 350);
    });
  };
  onFilter('audit-action', (value) => { state.audit.action = value; });
  onFilter('audit-since', (value) => { state.audit.since = value; });
}

function registerSystemListeners() {
  const on = (id, handler) => {
    const node = $(id);
    if (node) node.addEventListener('click', handler);
  };
  on('refresh-ports', fetchPorts);
  on('refresh-updates', fetchUpdates);
  on('refresh-smart', fetchSmart);
}

function registerSecurityListeners() {
  const on = (id, event, handler) => {
    const node = $(id);
    if (node) node.addEventListener(event, handler);
  };

  // Tokens
  on('token-new', 'click', () => {
    $('token-reveal').classList.add('hidden');
    $('token-form-wrap').classList.remove('hidden');
    $('token-status').classList.add('hidden');
    $('token-name').focus();
  });
  on('token-cancel', 'click', () => $('token-form-wrap').classList.add('hidden'));
  on('refresh-tokens', 'click', fetchTokens);
  on('token-reveal-copy', 'click', () => {
    const value = $('token-reveal-value').textContent;
    if (value && value !== '—') copyToClipboard(value, 'Token');
  });
  $('token-form')?.addEventListener('submit', withBusy('token-save', 'Creando', submitToken));

  // Sesiones
  on('refresh-sessions', 'click', fetchSessions);

  // Segundo factor
  on('refresh-2fa', 'click', fetchTwoFactor);
  on('2fa-secret-copy', 'click', () => {
    if (state.twoFactorSecret) copyToClipboard(state.twoFactorSecret, 'Secreto');
  });
  on('2fa-otpauth-copy', 'click', () => {
    const value = $('2fa-otpauth').textContent;
    if (value && value !== '—') copyToClipboard(value, 'URL otpauth');
  });
  on('2fa-setup-cancel', 'click', discardTwoFactorSetup);
  $('2fa-enable-form')?.addEventListener('submit', withBusy('2fa-enable', 'Activando', enableTwoFactor));
  $('2fa-disable-form')?.addEventListener('submit', withBusy('2fa-disable', 'Desactivando', disableTwoFactor));
}

function registerContainerListeners() {
  const on = (id, handler) => {
    const node = $(id);
    if (node) node.addEventListener('click', handler);
  };
  on('container-detail-close', closeContainerDetail);
  on('container-detail-refresh', () => {
    if (state.containerDetailId !== null) openContainerDetail(state.containerDetailId);
  });
  on('refresh-docker', fetchDockerInventory);
  on('docker-prune', pruneDockerImages);

  const tail = $('container-logs-tail');
  if (tail) {
    tail.addEventListener('change', () => {
      if (state.containerDetailId !== null) openContainerDetail(state.containerDetailId);
    });
  }
}

async function initDashboard() {
  // El revelado al entrar en pantalla arranca de inmediato, antes de
  // esperar a la sesión: si dependiera de la red, los bloques se
  // quedarían a opacidad 0 mientras el backend responde.
  initScrollReveal();

  // Estado vacío de las alarmas desde el primer pintado: un panel de
  // alarmas en blanco mientras llegan los datos se lee como "sin
  // datos", que es justo lo contrario de lo que dice.
  renderAlarms();

  // La sesión se valida primero: si no es válida, apiFetch redirige al login.
  try {
    await fetchSession();
  } catch (_error) {
    // El 401 ya provoca la redirección; otros fallos se reflejan en el estado.
  }
  initChart();
  registerEventListeners();
  startUiClock();
  await checkHealth();
  await Promise.all([fetchMetrics(), fetchContainers()]);
  startPolling();
  renderCountdown();
  renderFreshness();

  // El inventario de la flota alimenta el selector de la cabecera, así
  // que se pide al entrar y no solo al abrir la pestaña de Hosts. La
  // elección guardada se aplica después, cuando ya se sabe qué hosts
  // existen de verdad: aplicar un id antes de tiempo dejaría el
  // selector apuntando a un host que el hub no tiene.
  await fetchHosts();
  updateLocalOnlyNotices();
  const stored = readStoredHostId();
  if (stored && stored !== Number(state.hostId)) selectHost(stored);
  else renderHostScope();
  renderRollup();
}


document.addEventListener('DOMContentLoaded', initDashboard);
