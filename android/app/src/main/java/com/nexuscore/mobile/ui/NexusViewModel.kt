package com.nexuscore.mobile.ui

import android.app.Application
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.nexuscore.mobile.data.AlertEventDto
import com.nexuscore.mobile.data.AlertEventListDto
import com.nexuscore.mobile.data.AlertRuleDto
import com.nexuscore.mobile.data.ApiClient
import com.nexuscore.mobile.data.ApiException
import com.nexuscore.mobile.data.AuditEntryDto
import com.nexuscore.mobile.data.AuditListDto
import com.nexuscore.mobile.data.ChatLine
import com.nexuscore.mobile.data.ChatResponseDto
import com.nexuscore.mobile.data.ContainerDto
import com.nexuscore.mobile.data.EnrollmentDto
import com.nexuscore.mobile.data.HistoryDto
import com.nexuscore.mobile.data.HostDto
import com.nexuscore.mobile.data.MetricsDto
import com.nexuscore.mobile.data.TwoFactorSetupDto
import com.nexuscore.mobile.data.NetworkTokenDto
import com.nexuscore.mobile.data.NetworkPlanDto
import com.nexuscore.mobile.data.NetworkApplyDto
import com.nexuscore.mobile.data.AiTestDto
import com.nexuscore.mobile.data.AiSettingsDto
import com.nexuscore.mobile.data.MonitorDto
import com.nexuscore.mobile.data.NetworkStateDto
import com.nexuscore.mobile.data.PortsDto
import com.nexuscore.mobile.data.Repository
import com.nexuscore.mobile.data.SecurityAuditDto
import com.nexuscore.mobile.data.ServiceListDto
import com.nexuscore.mobile.data.SessionListDto
import com.nexuscore.mobile.data.SessionStore
import com.nexuscore.mobile.data.SmartDto
import com.nexuscore.mobile.data.TerminalTargetDto
import com.nexuscore.mobile.data.TokenListDto
import com.nexuscore.mobile.data.TwoFactorDto
import com.nexuscore.mobile.data.UnauthorizedException
import com.nexuscore.mobile.data.UpdatesDto
import com.nexuscore.mobile.worker.AlertWorker
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.TimeUnit
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager

class NexusViewModel(application: Application) : AndroidViewModel(application) {
    private val store = SessionStore(application)
    private val api = ApiClient(store)
    private val repo = Repository(api)
    private val json = Json { prettyPrint = true; encodeDefaults = true }

    var booting by mutableStateOf(true)
        private set
    var authenticated by mutableStateOf(false)
        private set
    var busy by mutableStateOf(false)
        private set
    var error by mutableStateOf<String?>(null)
        private set

    var baseUrl by mutableStateOf(store.baseUrl)
        private set
    var username by mutableStateOf(store.username)
        private set

    var section by mutableStateOf(Section.DASHBOARD)
        private set
    var hosts by mutableStateOf<List<HostDto>>(emptyList())
        private set
    var selectedHostId by mutableStateOf(1)
        private set

    var metrics by mutableStateOf<MetricsDto?>(null)
        private set
    var history by mutableStateOf<HistoryDto?>(null)
        private set
    var historyRange by mutableStateOf("1h")
        private set
    var historyMetric by mutableStateOf("cpu")
        private set
    var alertEvents by mutableStateOf<List<AlertEventDto>>(emptyList())
        private set
    var alertRules by mutableStateOf<List<AlertRuleDto>>(emptyList())
        private set
    var monitors by mutableStateOf<List<MonitorDto>>(emptyList())
        private set
    var securityAudit by mutableStateOf<SecurityAuditDto?>(null)
        private set
    var auditEntries by mutableStateOf<List<AuditEntryDto>>(emptyList())
        private set
    var containers by mutableStateOf<List<ContainerDto>>(emptyList())
        private set
    var services by mutableStateOf<ServiceListDto?>(null)
        private set
    var network by mutableStateOf<NetworkStateDto?>(null)
        private set
    var ports by mutableStateOf<PortsDto?>(null)
        private set
    var updates by mutableStateOf<UpdatesDto?>(null)
        private set
    var smart by mutableStateOf<SmartDto?>(null)
        private set
    var sessions by mutableStateOf<SessionListDto?>(null)
        private set
    var tokens by mutableStateOf<TokenListDto?>(null)
        private set
    var twoFactor by mutableStateOf<TwoFactorDto?>(null)
        private set
    var terminalTargets by mutableStateOf<List<TerminalTargetDto>>(emptyList())
        private set
    var aiState by mutableStateOf<JsonElement?>(null)
        private set
    var aiSettings by mutableStateOf<AiSettingsDto?>(null)
        private set
    var aiTest by mutableStateOf<AiTestDto?>(null)
        private set
    var twoFactorSetup by mutableStateOf<TwoFactorSetupDto?>(null)
        private set
    var networkPlan by mutableStateOf<NetworkPlanDto?>(null)
        private set
    var networkApply by mutableStateOf<NetworkApplyDto?>(null)
        private set
    var networkToken by mutableStateOf<String?>(null)
        private set
    var networkChangeError by mutableStateOf<String?>(null)
        private set
    var detailTitle by mutableStateOf<String?>(null)
        private set
    var detailBody by mutableStateOf<String?>(null)
        private set

    var chatLines by mutableStateOf<List<ChatLine>>(emptyList())
        private set
    var chatPending by mutableStateOf<List<JsonElement>>(emptyList())
        private set
    var chatSending by mutableStateOf(false)
        private set
    var chatError by mutableStateOf<String?>(null)
        private set

    val selectedHost: HostDto?
        get() = hosts.firstOrNull { it.id == selectedHostId }
            ?: hosts.firstOrNull()

    init {
        scheduleWorker()
        viewModelScope.launch {
            booting = true
            try {
                if (api.verifySession()) {
                    authenticated = true
                    loadHostsInternal()
                    loadSectionInternal(Section.DASHBOARD)
                }
            } catch (exc: Throwable) {
                error = exc.message
            } finally {
                booting = false
            }
        }
    }

    fun login(baseUrl: String, username: String, password: String, totp: String) {
        viewModelScope.launch {
            busy = true
            error = null
            try {
                api.login(baseUrl, username, password, totp)
                this@NexusViewModel.baseUrl = baseUrl.trim().trimEnd('/')
                this@NexusViewModel.username = username
                authenticated = true
                loadHostsInternal()
                loadSectionInternal(Section.DASHBOARD)
            } catch (exc: Throwable) {
                error = exc.message ?: "No se pudo iniciar sesión"
            } finally {
                busy = false
            }
        }
    }

    fun logout() {
        viewModelScope.launch {
            runCatching { api.logout() }
            authenticated = false
            hosts = emptyList()
            metrics = null
            history = null
            chatLines = emptyList()
            chatPending = emptyList()
            detailTitle = null
            detailBody = null
            error = null
        }
    }

    fun selectSection(value: Section) {
        if (section == value && dataFor(value) != null) return
        section = value
        loadSection(value)
    }

    fun selectHost(hostId: Int) {
        if (selectedHostId == hostId) return
        selectedHostId = hostId
        metrics = null
        history = null
        containers = emptyList()
        services = null
        network = null
        ports = null
        updates = null
        smart = null
        loadSection(section)
    }

    fun refresh() {
        loadSection(section, force = true)
    }

    fun changeHistoryRange(value: String) {
        if (historyRange == value) return
        historyRange = value
        loadHistory()
    }

    fun changeHistoryMetric(value: String) {
        if (historyMetric == value) return
        historyMetric = value
        loadHistory()
    }

    fun clearError() {
        error = null
    }

    fun clearDetail() {
        detailTitle = null
        detailBody = null
    }

    fun openTerminal(hostId: Int, listener: WebSocketListener): WebSocket? =
        runCatching { api.openTerminal(hostId, listener) }
            .onFailure { error = it.message }
            .getOrNull()

    fun sendChat(message: String) {
        if (message.isBlank() || chatSending) return
        val prior = chatLines
        chatLines = prior + ChatLine("user", message)
        chatSending = true
        chatError = null
        viewModelScope.launch {
            try {
                val response = repo.chat(message, prior)
                chatLines = chatLines + ChatLine("assistant", response.reply)
                chatPending = response.pendingActions
            } catch (exc: Throwable) {
                chatError = exc.message ?: "Error del asistente"
            } finally {
                chatSending = false
            }
        }
    }

    fun confirmPending(id: String, confirm: Boolean) {
        viewModelScope.launch {
            try {
                repo.confirmPending(id, confirm)
                chatPending = chatPending.filter {
                    it.toString().contains(id).not()
                }
            } catch (exc: Throwable) {
                chatError = exc.message
            }
        }
    }

    fun ackEvent(event: AlertEventDto) = action("Alerta reconocida") {
        repo.ackAlert(event.id)
        loadAlertsInternal()
    }

    fun snoozeEvent(event: AlertEventDto) = action("Alerta aplazada 15 min") {
        repo.snoozeAlert(event.id, 15)
        loadAlertsInternal()
    }

    fun createRule(name: String, metric: String, op: String, threshold: Double, duration: Int) =
        action("Regla creada") {
            repo.createRule(name, metric, op, threshold, duration)
            loadAlertsInternal()
        }

    fun toggleRule(rule: AlertRuleDto) = action("Regla actualizada") {
        repo.setRuleEnabled(rule.id, rule.enabled == 0)
        loadAlertsInternal()
    }

    fun deleteRule(rule: AlertRuleDto) = action("Regla eliminada") {
        repo.deleteRule(rule.id)
        loadAlertsInternal()
    }

    fun createMonitor(name: String, kind: String, target: String, port: Int?, interval: Int, timeout: Int) =
        action("Monitor creado") {
            repo.createMonitor(name, kind, target, port, interval, timeout)
            loadMonitorsInternal()
        }

    fun toggleMonitor(monitor: MonitorDto) = action("Monitor actualizado") {
        repo.setMonitorEnabled(monitor.id, monitor.enabled == 0)
        loadMonitorsInternal()
    }

    fun deleteMonitor(monitor: MonitorDto) = action("Monitor eliminado") {
        repo.deleteMonitor(monitor.id)
        loadMonitorsInternal()
    }

    fun runSecurityAudit() {
        viewModelScope.launch {
            busy = true
            error = null
            try {
                securityAudit = repo.securityAudit(force = true)
            } catch (exc: Throwable) {
                handle(exc)
            } finally {
                busy = false
            }
        }
    }

    fun saveBaseline() = action("Baseline guardado") { repo.saveSecurityBaseline() }
    fun clearBaseline() = action("Baseline borrado") { repo.clearSecurityBaseline() }

    fun serviceAction(service: String, action: String) = action("Servicio $action") {
        repo.serviceAction(selectedHostId, service, action)
        loadServicesInternal()
    }

    fun showServiceLogs(service: String) {
        viewModelScope.launch {
            try {
                val element = repo.serviceLogs(selectedHostId, service)
                showDetail("Logs · $service", pretty(element))
            } catch (exc: Throwable) {
                handle(exc)
            }
        }
    }

    fun showContainerLogs(container: ContainerDto) {
        viewModelScope.launch {
            try {
                val element = repo.containerLogs(selectedHostId, container.id)
                showDetail("Logs · ${container.name}", pretty(element))
            } catch (exc: Throwable) {
                handle(exc)
            }
        }
    }

    fun showContainerStats(container: ContainerDto) {
        viewModelScope.launch {
            try {
                val element = repo.containerStats(selectedHostId, container.id)
                showDetail("Stats · ${container.name}", pretty(element))
            } catch (exc: Throwable) {
                handle(exc)
            }
        }
    }

    fun removeContainer(container: ContainerDto) = action("Contenedor eliminado") {
        repo.removeContainer(selectedHostId, container.id)
        loadContainersInternal()
    }

    fun deployContainer(image: String, port: Int?, name: String?) = action("Contenedor desplegado") {
        repo.deployContainer(selectedHostId, image, port, name)
        loadContainersInternal()
    }

    fun pruneContainers() = action("Limpieza Docker solicitada") {
        repo.pruneContainers(selectedHostId)
        loadContainersInternal()
    }

    fun scanNetwork(subnet: String) = action("Escaneo completado") {
        val element = repo.scanNetwork(selectedHostId, subnet)
        showDetail("Resultado del escaneo", pretty(element))
    }

    fun showEnrollment() {
        viewModelScope.launch {
            try {
                val info = repo.enrollment()
                showDetail(
                    "Token de enrolamiento",
                    "${info.command}\n\n${info.warning}\n\n${info.token}",
                )
            } catch (exc: Throwable) {
                handle(exc)
            }
        }
    }

    fun updateHost(host: HostDto, name: String, notes: String) = action("Host actualizado") {
        repo.updateHost(host.id, name, notes, null)
        loadHostsInternal()
    }

    fun deleteHost(host: HostDto) = action("Host eliminado") {
        repo.deleteHost(host.id)
        loadHostsInternal()
    }

    fun revokeSession(id: String) = action("Sesión revocada") {
        repo.revokeSession(id)
        loadSecurityInternal()
    }

    fun createToken(name: String, scope: String) {
        viewModelScope.launch {
            try {
                val response = repo.createToken(name, scope)
                showDetail("Token creado (cópialo ahora)", pretty(response))
                loadSecurityInternal()
            } catch (exc: Throwable) {
                handle(exc)
            }
        }
    }

    fun revokeToken(id: Int) = action("Token revocado") {
        repo.revokeToken(id)
        loadSecurityInternal()
    }

    fun showDetail(title: String, body: String) {
        detailTitle = title
        detailBody = body
    }

    fun clearNetworkChange() {
        networkPlan = null
        networkApply = null
        networkToken = null
        networkChangeError = null
    }

    fun planNetwork(
        interfaceName: String,
        address: String,
        prefix: Int,
        gateway: String,
        dns: List<String>,
    ) {
        viewModelScope.launch {
            busy = true
            networkChangeError = null
            try {
                networkPlan = repo.planNetwork(interfaceName, address, prefix, gateway, dns)
                networkApply = null
                networkToken = null
            } catch (exc: Throwable) {
                networkChangeError = exc.message
            } finally {
                busy = false
            }
        }
    }

    fun applyNetwork(
        interfaceName: String,
        address: String,
        prefix: Int,
        gateway: String,
        dns: List<String>,
    ) {
        viewModelScope.launch {
            busy = true
            networkChangeError = null
            try {
                val result = repo.applyNetwork(interfaceName, address, prefix, gateway, dns)
                networkApply = result
                networkToken = result.token
                if (!result.success) networkChangeError = result.message
            } catch (exc: Throwable) {
                networkChangeError = exc.message
            } finally {
                busy = false
            }
        }
    }

    fun confirmNetwork() {
        val token = networkToken ?: return
        viewModelScope.launch {
            busy = true
            networkChangeError = null
            try {
                val result = repo.confirmNetwork(token)
                showDetail("Red confirmada", result.message)
                clearNetworkChange()
            } catch (exc: Throwable) {
                networkChangeError = exc.message
            } finally {
                busy = false
            }
        }
    }

    fun revertNetwork() {
        val token = networkToken ?: return
        viewModelScope.launch {
            busy = true
            networkChangeError = null
            try {
                val result = repo.revertNetwork(token)
                showDetail("Red revertida", result.message)
                clearNetworkChange()
            } catch (exc: Throwable) {
                networkChangeError = exc.message
            } finally {
                busy = false
            }
        }
    }

    fun startTwoFactorSetup() {
        viewModelScope.launch {
            busy = true
            error = null
            try {
                twoFactorSetup = repo.setupTwoFactor()
                loadSecurityInternal()
            } catch (exc: Throwable) {
                handle(exc)
            } finally {
                busy = false
            }
        }
    }

    fun clearTwoFactorSetup() {
        twoFactorSetup = null
        viewModelScope.launch {
            runCatching { loadSecurityInternal() }
        }
    }

    fun enableTwoFactor(code: String) {
        viewModelScope.launch {
            busy = true
            error = null
            try {
                repo.enableTwoFactor(code)
                twoFactorSetup = null
                loadSecurityInternal()
                showDetail("2FA activado", "El segundo factor queda activo y los códigos de recuperación deben guardarse.")
            } catch (exc: Throwable) {
                handle(exc)
            } finally {
                busy = false
            }
        }
    }

    fun disableTwoFactor(password: String) {
        viewModelScope.launch {
            busy = true
            error = null
            try {
                repo.disableTwoFactor(password)
                twoFactorSetup = null
                loadSecurityInternal()
                showDetail("2FA desactivado", "La cuenta vuelve a proteger solo con contraseña.")
            } catch (exc: Throwable) {
                handle(exc)
            } finally {
                busy = false
            }
        }
    }

    fun saveAiSettings(
        provider: String,
        model: String,
        temperature: Double,
        openaiBaseUrl: String,
        geminiBaseUrl: String,
        ollamaBaseUrl: String,
        openaiKey: String,
        anthropicKey: String,
        geminiKey: String,
    ) {
        viewModelScope.launch {
            busy = true
            error = null
            try {
                aiSettings = repo.updateAiSettings(
                    provider,
                    model,
                    temperature,
                    openaiBaseUrl,
                    geminiBaseUrl,
                    ollamaBaseUrl,
                    openaiKey,
                    anthropicKey,
                    geminiKey,
                )
                aiTest = null
                showDetail("IA actualizada", "La configuración se guardó y se aplicó en caliente.")
            } catch (exc: Throwable) {
                handle(exc)
            } finally {
                busy = false
            }
        }
    }

    fun testAi() {
        viewModelScope.launch {
            busy = true
            error = null
            try {
                aiTest = repo.testAi()
            } catch (exc: Throwable) {
                handle(exc)
            } finally {
                busy = false
            }
        }
    }

    private fun action(successMessage: String, block: suspend () -> Unit) {
        viewModelScope.launch {
            busy = true
            error = null
            try {
                block()
                showDetail("Hecho", successMessage)
            } catch (exc: Throwable) {
                handle(exc)
            } finally {
                busy = false
            }
        }
    }

    private fun loadSection(value: Section, force: Boolean = false) {
        viewModelScope.launch {
            busy = true
            error = null
            try {
                loadSectionInternal(value, force)
            } catch (exc: Throwable) {
                handle(exc)
            } finally {
                busy = false
            }
        }
    }

    private suspend fun loadSectionInternal(value: Section, force: Boolean = false) {
        when (value) {
            Section.DASHBOARD -> loadDashboardInternal()
            Section.HOSTS -> loadHostsInternal()
            Section.CONTAINERS -> loadContainersInternal()
            Section.SERVICES -> loadServicesInternal()
            Section.NETWORK -> loadNetworkInternal()
            Section.ALERTS -> loadAlertsInternal()
            Section.HISTORY -> loadHistoryInternal()
            Section.MONITORS -> loadMonitorsInternal()
            Section.AUDIT -> loadAuditInternal(force)
            Section.SYSTEM -> loadSystemInternal()
            Section.SECURITY -> loadSecurityInternal()
            Section.TERMINAL -> loadTerminalInternal()
            Section.ASSISTANT -> Unit
            Section.SETTINGS -> loadSettingsInternal()
        }
    }

    private suspend fun loadHostsInternal() {
        val data = repo.hosts()
        hosts = data.hosts
        if (hosts.none { it.id == selectedHostId }) {
            selectedHostId = hosts.firstOrNull { it.isLocal == 1 }?.id
                ?: hosts.firstOrNull()?.id
                ?: 1
        }
    }

    private suspend fun loadDashboardInternal() {
        val hostId = selectedHostId
        metrics = repo.metrics(hostId)
        history = repo.history(hostId, "1h", "cpu,ram")
    }

    private fun loadHistory() {
        viewModelScope.launch {
            busy = true
            error = null
            try {
                loadHistoryInternal()
            } catch (exc: Throwable) {
                handle(exc)
            } finally {
                busy = false
            }
        }
    }

    private suspend fun loadHistoryInternal() {
        history = repo.history(selectedHostId, historyRange, historyMetric)
    }

    private suspend fun loadAlertsInternal() {
        val events = repo.alertEvents()
        alertEvents = events.events
        alertRules = repo.alertRules().rules
    }

    private suspend fun loadMonitorsInternal() {
        monitors = repo.monitors().monitors
    }

    private suspend fun loadContainersInternal() {
        containers = repo.containers(selectedHostId)
    }

    private suspend fun loadServicesInternal() {
        services = repo.services(selectedHostId)
    }

    private suspend fun loadNetworkInternal() {
        network = repo.network(selectedHostId)
    }

    private suspend fun loadSystemInternal() {
        ports = repo.ports(selectedHostId)
        updates = repo.updates(selectedHostId)
        smart = repo.smart(selectedHostId)
    }

    private suspend fun loadAuditInternal(force: Boolean) {
        auditEntries = repo.audit().events
        securityAudit = repo.securityAudit(force = force)
    }

    private suspend fun loadSecurityInternal() {
        sessions = repo.sessions()
        tokens = repo.tokens()
        twoFactor = repo.twoFactor()
    }

    private suspend fun loadTerminalInternal() {
        terminalTargets = repo.terminalTargets().targets
    }

    private suspend fun loadSettingsInternal() {
        aiState = runCatching { repo.aiState() }.getOrNull()
        aiSettings = runCatching { repo.aiSettings() }.getOrNull()
    }

    private fun dataFor(section: Section): Any? = when (section) {
        Section.DASHBOARD -> metrics
        Section.HOSTS -> hosts
        Section.ALERTS -> alertEvents
        Section.MONITORS -> monitors
        Section.CONTAINERS -> containers
        Section.SERVICES -> services
        Section.NETWORK -> network
        Section.HISTORY -> history
        Section.AUDIT -> securityAudit
        Section.SYSTEM -> ports
        Section.SECURITY -> sessions
        Section.TERMINAL -> terminalTargets
        Section.ASSISTANT -> chatLines
        Section.SETTINGS -> aiState
    }

    private fun handle(exc: Throwable) {
        if (exc is UnauthorizedException) {
            authenticated = false
            store.clear()
            error = "Sesión caducada. Vuelve a iniciar sesión."
            return
        }
        error = (exc as? ApiException)?.message ?: exc.message ?: "Error inesperado"
    }

    private fun pretty(element: JsonElement): String = runCatching {
        json.encodeToString(JsonElement.serializer(), element)
    }.getOrElse { element.toString() }

    private fun scheduleWorker() {
        val request = PeriodicWorkRequestBuilder<AlertWorker>(15, TimeUnit.MINUTES).build()
        WorkManager.getInstance(getApplication()).enqueueUniquePeriodicWork(
            "nexus-alert-notifications",
            ExistingPeriodicWorkPolicy.UPDATE,
            request,
        )
    }
}
