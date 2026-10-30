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
import com.nexuscore.mobile.ui.components.formatTimestamp

@Composable
fun AlertsScreen(vm: NexusViewModel) {
    var tab by remember { mutableIntStateOf(0) }
    var showRuleDialog by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxWidth()) {
        TabRow(selectedTabIndex = tab) {
            Tab(selected = tab == 0, onClick = { tab = 0 }, text = { Text("Eventos") })
            Tab(selected = tab == 1, onClick = { tab = 1 }, text = { Text("Reglas") })
        }
        LazyColumn(
            contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            if (tab == 0) {
                if (vm.alertEvents.isEmpty()) {
                    item { EmptyState("Sin eventos", "No hay alertas registradas.") }
                }
                items(vm.alertEvents, key = { it.id }) { event ->
                    AlertEventCard(event, vm)
                }
            } else {
                item {
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Button(onClick = { showRuleDialog = true }, modifier = Modifier.weight(1f)) {
                            Text("Nueva regla")
                        }
                        OutlinedButton(onClick = { vm.refresh() }, modifier = Modifier.weight(1f)) {
                            Text("Refrescar")
                        }
                    }
                }
                if (vm.alertRules.isEmpty()) {
                    item { EmptyState("Sin reglas", "Crea una regla de alerta.") }
                }
                items(vm.alertRules, key = { it.id }) { rule ->
                    AlertRuleCard(rule, vm)
                }
            }
        }
    }
    if (showRuleDialog) {
        RuleDialog(
            onDismiss = { showRuleDialog = false },
            onCreate = { name, metric, op, threshold, duration ->
                vm.createRule(name, metric, op, threshold, duration)
                showRuleDialog = false
            },
        )
    }
}

@Composable
private fun AlertEventCard(event: AlertEventDto, vm: NexusViewModel) {
    val color = when (event.severity) {
        "crit" -> MaterialTheme.colorScheme.error
        "warn" -> MaterialTheme.colorScheme.secondary
        else -> MaterialTheme.colorScheme.primary
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
                StatusPill(event.severity, color)
            }
            Text(
                event.message ?: "Sin mensaje",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 6.dp),
            )
            Spacer(Modifier.height(6.dp))
            KeyValue("Estado", event.status)
            KeyValue("Valor", event.value?.toString() ?: "—")
            KeyValue("Umbral", event.threshold?.toString() ?: "—")
            KeyValue("Abierta", formatTimestamp(event.openedAt))
            if (event.status == "open") {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = { vm.ackEvent(event) }) { Text("Reconocer") }
                    TextButton(onClick = { vm.snoozeEvent(event) }) { Text("Aplazar 15 min") }
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
                OutlinedButton(onClick = { vm.toggleRule(rule) }) {
                    Text(if (rule.enabled == 1) "Desactivar" else "Activar")
                }
                TextButton(onClick = { vm.deleteRule(rule) }) { Text("Eliminar") }
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
            Button(
                onClick = {
                    onCreate(name, metric, op, thresholdValue ?: 0.0, durationValue ?: 0)
                },
                enabled = name.isNotBlank() && thresholdValue != null && durationValue != null,
            ) { Text("Crear") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
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
                Button(onClick = { showDialog = true }, modifier = Modifier.weight(1f)) {
                    Text("Nuevo monitor")
                }
                OutlinedButton(onClick = { vm.refresh() }, modifier = Modifier.weight(1f)) {
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
                OutlinedButton(onClick = { vm.toggleMonitor(monitor) }) {
                    Text(if (monitor.enabled == 1) "Desactivar" else "Activar")
                }
                TextButton(onClick = { vm.deleteMonitor(monitor) }) { Text("Eliminar") }
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
            Button(
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
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}
