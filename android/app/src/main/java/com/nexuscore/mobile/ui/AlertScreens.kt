package com.nexuscore.mobile.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.horizontalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.nexuscore.mobile.data.AlertEventDto
import com.nexuscore.mobile.data.AlertRuleDto
import com.nexuscore.mobile.data.MonitorDto
import com.nexuscore.mobile.ui.components.EmptyState
import com.nexuscore.mobile.ui.components.KeyValue
import com.nexuscore.mobile.ui.components.NexusCard
import com.nexuscore.mobile.ui.components.StatusPill
import com.nexuscore.mobile.ui.components.statusColor
import com.nexuscore.mobile.ui.components.formatTimestamp

import androidx.compose.material3.ScrollableTabRow
import com.nexuscore.mobile.data.MaintenanceWindowDto
import com.nexuscore.mobile.data.NotificationChannelDto
import com.nexuscore.mobile.data.RemediationProposalDto
@Composable
fun AlertsScreen(vm: NexusViewModel) {
    var tab by remember { mutableIntStateOf(0) }
    var showRuleDialog by remember { mutableStateOf(false) }
    var showChannelDialog by remember { mutableStateOf(false) }
    var showWindowDialog by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxWidth()) {
        ScrollableTabRow(selectedTabIndex = tab, edgePadding = 12.dp) {
            Tab(selected = tab == 0, onClick = { tab = 0 }, text = { Text("Eventos") })
            Tab(selected = tab == 1, onClick = { tab = 1 }, text = { Text("Reglas") })
            Tab(selected = tab == 2, onClick = { tab = 2 }, text = { Text("Canales") })
            Tab(selected = tab == 3, onClick = { tab = 3 }, text = { Text("Mantenimiento") })
            Tab(selected = tab == 4, onClick = { tab = 4 }, text = { Text("Propuestas") })
        }
        LazyColumn(
            contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            when (tab) {
                0 -> {
                    if (vm.alertEvents.isEmpty()) item { EmptyState("Sin eventos", "No hay alertas registradas.") }
                    items(vm.alertEvents, key = { it.id }) { event -> AlertEventCard(event, vm) }
                }
                1 -> {
                    item {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            NexusButton(onClick = { showRuleDialog = true }, modifier = Modifier.weight(1f)) { Text("Nueva regla") }
                            NexusOutlinedButton(onClick = { vm.refresh() }, modifier = Modifier.weight(1f)) { Text("Refrescar") }
                        }
                    }
                    if (vm.alertRules.isEmpty()) item { EmptyState("Sin reglas", "Crea una regla de alerta.") }
                    items(vm.alertRules, key = { it.id }) { rule -> AlertRuleCard(rule, vm) }
                }
                2 -> {
                    item {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            NexusButton(onClick = { showChannelDialog = true }, modifier = Modifier.weight(1f)) { Text("Nuevo webhook") }
                            NexusOutlinedButton(onClick = { vm.refresh() }, modifier = Modifier.weight(1f)) { Text("Refrescar") }
                        }
                    }
                    val channels = vm.notificationChannels?.channels
                    if (channels == null) item { EmptyState("Cargando canales…") }
                    else if (channels.isEmpty()) item { EmptyState("Sin canales", "Añade un webhook de notificación.") }
                    else items(channels, key = { it.id }) { channel -> ChannelCard(channel, vm) }
                }
                3 -> {
                    item {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            NexusButton(onClick = { showWindowDialog = true }, modifier = Modifier.weight(1f)) { Text("Nueva ventana") }
                            NexusOutlinedButton(onClick = { vm.refresh() }, modifier = Modifier.weight(1f)) { Text("Refrescar") }
                        }
                    }
                    val windows = vm.maintenanceWindows?.windows
                    if (windows == null) item { EmptyState("Cargando ventanas…") }
                    else if (windows.isEmpty()) item { EmptyState("Sin ventanas", "No hay mantenimiento programado.") }
                    else items(windows, key = { it.id }) { window -> MaintenanceCard(window, vm) }
                }
                else -> {
                    item {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            NexusOutlinedButton(onClick = { vm.refresh() }, modifier = Modifier.fillMaxWidth()) { Text("Refrescar propuestas") }
                        }
                    }
                    val proposals = vm.remediationProposals?.proposals
                    if (proposals == null) item { EmptyState("Cargando propuestas…") }
                    else if (proposals.isEmpty()) item { EmptyState("Nada pendiente", "No hay propuestas esperando confirmación.") }
                    else items(proposals, key = { it.id }) { proposal -> ProposalCard(proposal, vm) }
                }
            }
        }
    }
    if (showRuleDialog) RuleDialog(onDismiss = { showRuleDialog = false }, onCreate = { name, metric, op, threshold, duration -> vm.createRule(name, metric, op, threshold, duration); showRuleDialog = false })
    if (showChannelDialog) WebhookChannelDialog(onDismiss = { showChannelDialog = false }, onCreate = { name, url -> vm.createNotificationChannel(name, url); showChannelDialog = false })
    if (showWindowDialog) MaintenanceWindowDialog(onDismiss = { showWindowDialog = false }, onCreate = { name, start, end -> vm.createMaintenanceWindow(name, start, end); showWindowDialog = false })
}

@Composable
private fun ChannelCard(channel: NotificationChannelDto, vm: NexusViewModel) {
    NexusCard {
        Column(Modifier.padding(14.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(channel.name, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                StatusPill(if (channel.enabled == 1) "activo" else "inactivo", if (channel.enabled == 1) MaterialTheme.colorScheme.tertiary else MaterialTheme.colorScheme.outline)
            }
            KeyValue("Tipo", channel.kind)
            channel.lastError?.takeIf { it.isNotBlank() }?.let { KeyValue("Último error", it.take(180)) }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                NexusOutlinedButton(onClick = { vm.testNotificationChannel(channel.id) }) { Text("Probar") }
                NexusTextButton(onClick = { vm.deleteNotificationChannel(channel.id) }) { Text("Eliminar") }
            }
        }
    }
}

@Composable
private fun MaintenanceCard(window: MaintenanceWindowDto, vm: NexusViewModel) {
    NexusCard {
        Column(Modifier.padding(14.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(window.name, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                StatusPill(if (window.enabled == 1) "activa" else "inactiva", if (window.enabled == 1) MaterialTheme.colorScheme.tertiary else MaterialTheme.colorScheme.outline)
            }
            KeyValue("Inicio", formatTimestamp(window.startsAt))
            KeyValue("Fin", formatTimestamp(window.endsAt))
            NexusTextButton(onClick = { vm.deleteMaintenanceWindow(window.id) }) { Text("Eliminar") }
        }
    }
}

@Composable
private fun ProposalCard(proposal: RemediationProposalDto, vm: NexusViewModel) {
    NexusCard {
        Column(Modifier.padding(14.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(proposal.action, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                StatusPill("pendiente", MaterialTheme.colorScheme.secondary)
            }
            Text(proposal.summary, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 4.dp))
            KeyValue("Regla", proposal.ruleName.ifBlank { proposal.ruleId.toString() })
            KeyValue("Caduca", formatTimestamp(proposal.expiresAt))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                NexusButton(onClick = { vm.resolveProposal(proposal.id, true) }) { Text("Confirmar") }
                NexusOutlinedButton(onClick = { vm.resolveProposal(proposal.id, false) }) { Text("Cancelar") }
            }
        }
    }
}

@Composable
private fun WebhookChannelDialog(onDismiss: () -> Unit, onCreate: (String, String) -> Unit) {
    var name by remember { mutableStateOf("") }
    var url by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Nuevo canal webhook") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(value = name, onValueChange = { name = it }, label = { Text("Nombre") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(value = url, onValueChange = { url = it }, label = { Text("URL HTTPS") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                Text("El canal enviará las notificaciones a esa URL.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        },
        confirmButton = { NexusButton(onClick = { onCreate(name.trim(), url.trim()) }, enabled = name.isNotBlank() && url.startsWith("https://")) { Text("Crear") } },
        dismissButton = { NexusTextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}

@Composable
private fun MaintenanceWindowDialog(onDismiss: () -> Unit, onCreate: (String, String, String) -> Unit) {
    var name by remember { mutableStateOf("") }
    var start by remember { mutableStateOf("") }
    var end by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Nueva ventana de mantenimiento") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(value = name, onValueChange = { name = it }, label = { Text("Motivo") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(value = start, onValueChange = { start = it }, label = { Text("Inicio ISO-8601") }, placeholder = { Text("2026-11-13T22:00:00Z") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(value = end, onValueChange = { end = it }, label = { Text("Fin ISO-8601") }, placeholder = { Text("2026-11-13T23:00:00Z") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                Text("Durante la ventana los eventos se registran, pero no se notifican.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        },
        confirmButton = { NexusButton(onClick = { onCreate(name.trim(), start.trim(), end.trim()) }, enabled = name.isNotBlank() && start.isNotBlank() && end.isNotBlank()) { Text("Crear") } },
        dismissButton = { NexusTextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}

@Composable
private fun AlertEventCard(event: AlertEventDto, vm: NexusViewModel) {
    val color = when (event.severity) {
        "crit" -> statusColor("crit")
        "warn" -> statusColor("warn")
        else -> MaterialTheme.colorScheme.onSurfaceVariant
    }
    val severity = when (event.severity) {
        "crit" -> "Cr?tica"
        "warn" -> "Aviso"
        else -> event.severity
    }
    val state = when (event.status) {
        "open" -> "Abierta"
        "closed" -> "Cerrada"
        "acked" -> "Reconocida"
        else -> event.status
    }
    NexusCard {
        Column(Modifier.padding(14.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(
                    event.metric?.uppercase() ?: "ALERTA",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                StatusPill(severity, color)
            }
            Text(
                event.message ?: "Sin mensaje",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 6.dp),
            )
            Spacer(Modifier.height(6.dp))
            KeyValue("Estado", state)
            KeyValue("Valor", event.value?.toString() ?: "—")
            KeyValue("Umbral", event.threshold?.toString() ?: "—")
            KeyValue("Abierta", formatTimestamp(event.openedAt))
            if (event.status == "open") {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    NexusOutlinedButton(onClick = { vm.ackEvent(event) }) { Text("Reconocer") }
                    NexusTextButton(onClick = { vm.snoozeEvent(event) }) { Text("Aplazar 15 min") }
                }
            }
        }
    }
}

@Composable
private fun AlertRuleCard(rule: AlertRuleDto, vm: NexusViewModel) {
    NexusCard {
        Column(Modifier.padding(14.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(
                    rule.name,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                StatusPill(
                    if (rule.enabled == 1) "activa" else "inactiva",
                    if (rule.enabled == 1) MaterialTheme.colorScheme.tertiary else MaterialTheme.colorScheme.outline,
                )
            }
            KeyValue("Condición", "${rule.metric} ${rule.op} ${rule.threshold}")
            KeyValue("Duración", "${rule.durationS} s")
            KeyValue("Recuperación", rule.clearThreshold?.toString() ?: "—")
            KeyValue("Host", rule.hostId?.toString() ?: "todos")
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                NexusOutlinedButton(onClick = { vm.toggleRule(rule) }) {
                    Text(if (rule.enabled == 1) "Desactivar" else "Activar")
                }
                NexusTextButton(onClick = { vm.deleteRule(rule) }) { Text("Eliminar") }
            }
        }
    }
}

@Composable
private fun RuleDialog(
    onDismiss: () -> Unit,
    onCreate: (String, String, String, Double, Int) -> Unit,
) {
    var name by remember { mutableStateOf("") }
    var metric by remember { mutableStateOf("cpu") }
    var op by remember { mutableStateOf(">") }
    var threshold by remember { mutableStateOf("90") }
    var duration by remember { mutableStateOf("300") }
    val thresholdValue = threshold.toDoubleOrNull()
    val durationValue = duration.toIntOrNull()
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Nueva regla") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("Nombre") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(
                        value = metric,
                        onValueChange = { metric = it },
                        label = { Text("Métrica") },
                        singleLine = true,
                        modifier = Modifier.weight(1f),
                    )
                    OutlinedTextField(
                        value = op,
                        onValueChange = { op = it },
                        label = { Text("Operador") },
                        singleLine = true,
                        modifier = Modifier.weight(0.6f),
                    )
                }
                OutlinedTextField(
                    value = threshold,
                    onValueChange = { threshold = it },
                    label = { Text("Umbral") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = duration,
                    onValueChange = { duration = it },
                    label = { Text("Duración mínima (s)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            NexusButton(
                onClick = {
                    onCreate(name, metric, op, thresholdValue ?: 0.0, durationValue ?: 0)
                },
                enabled = name.isNotBlank() && thresholdValue != null && durationValue != null,
            ) { Text("Crear") }
        },
        dismissButton = { NexusTextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}

@Composable
fun MonitorsScreen(vm: NexusViewModel) {
    var showDialog by remember { mutableStateOf(false) }
    LazyColumn(
        contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                NexusButton(onClick = { showDialog = true }, modifier = Modifier.weight(1f)) {
                    Text("Nuevo monitor")
                }
                NexusOutlinedButton(onClick = { vm.refresh() }, modifier = Modifier.weight(1f)) {
                    Text("Refrescar")
                }
            }
        }
        if (vm.monitors.isEmpty()) {
            item { EmptyState("Sin monitores", "Vigila HTTP, TCP o certificados SSL.") }
        }
        items(vm.monitors, key = { it.id }) { monitor ->
            MonitorCard(monitor, vm)
        }
    }
    if (showDialog) {
        MonitorDialog(
            onDismiss = { showDialog = false },
            onCreate = { name, kind, target, port, interval, timeout ->
                vm.createMonitor(name, kind, target, port, interval, timeout)
                showDialog = false
            },
        )
    }
}

@Composable
private fun MonitorCard(monitor: MonitorDto, vm: NexusViewModel) {
    val color = when (monitor.status.lowercase()) {
        "up", "online" -> MaterialTheme.colorScheme.tertiary
        "down", "offline" -> MaterialTheme.colorScheme.error
        else -> MaterialTheme.colorScheme.secondary
    }
    NexusCard {
        Column(Modifier.padding(14.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        monitor.name,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        "${monitor.kind.uppercase()} · ${monitor.target}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                StatusPill(monitor.status, color)
            }
            Spacer(Modifier.height(8.dp))
            KeyValue("Latencia", monitor.latencyMs?.let { "$it ms" } ?: "—")
            KeyValue("Último check", formatTimestamp(monitor.lastCheck))
            KeyValue("Uptime 24h", monitor.uptime24h?.let { "%.2f %%".format(it) } ?: "—")
            KeyValue("SSL", monitor.sslDaysLeft?.let { "$it días" } ?: "—")
            if (!monitor.lastError.isNullOrBlank()) {
                Text(
                    monitor.lastError,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.labelSmall,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                NexusOutlinedButton(onClick = { vm.toggleMonitor(monitor) }) {
                    Text(if (monitor.enabled == 1) "Desactivar" else "Activar")
                }
                NexusTextButton(onClick = { vm.deleteMonitor(monitor) }) { Text("Eliminar") }
            }
        }
    }
}

@Composable
private fun MonitorDialog(
    onDismiss: () -> Unit,
    onCreate: (String, String, String, Int?, Int, Int) -> Unit,
) {
    var name by remember { mutableStateOf("") }
    var kind by remember { mutableStateOf("http") }
    var target by remember { mutableStateOf("") }
    var port by remember { mutableStateOf("") }
    var interval by remember { mutableStateOf("60") }
    var timeout by remember { mutableStateOf("5") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Nuevo monitor") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("Nombre") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Row(
                    Modifier
                        .fillMaxWidth()
                        .horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    listOf("http", "tcp", "ssl").forEach { value ->
                        FilterChip(
                            selected = kind == value,
                            onClick = { kind = value },
                            label = { Text(value.uppercase()) },
                        )
                    }
                }
                OutlinedTextField(
                    value = target,
                    onValueChange = { target = it },
                    label = { Text("Destino (URL o host)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(
                        value = port,
                        onValueChange = { port = it },
                        label = { Text("Puerto") },
                        singleLine = true,
                        modifier = Modifier.weight(1f),
                    )
                    OutlinedTextField(
                        value = interval,
                        onValueChange = { interval = it },
                        label = { Text("Intervalo") },
                        singleLine = true,
                        modifier = Modifier.weight(1f),
                    )
                    OutlinedTextField(
                        value = timeout,
                        onValueChange = { timeout = it },
                        label = { Text("Timeout") },
                        singleLine = true,
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        },
        confirmButton = {
            NexusButton(
                onClick = {
                    onCreate(
                        name,
                        kind,
                        target,
                        port.toIntOrNull(),
                        interval.toIntOrNull() ?: 60,
                        timeout.toIntOrNull() ?: 5,
                    )
                },
                enabled = name.isNotBlank() && target.isNotBlank(),
            ) { Text("Crear") }
        },
        dismissButton = { NexusTextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}
