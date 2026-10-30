package com.nexuscore.mobile.ui

import androidx.compose.foundation.horizontalScroll
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
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.nexuscore.mobile.data.MetricPoint
import com.nexuscore.mobile.ui.components.EmptyState
import com.nexuscore.mobile.ui.components.KeyValue
import com.nexuscore.mobile.ui.components.LineChart
import com.nexuscore.mobile.ui.components.MetricCard
import com.nexuscore.mobile.ui.components.NexusCard
import com.nexuscore.mobile.ui.components.SectionTitle
import com.nexuscore.mobile.ui.components.SensorRow
import com.nexuscore.mobile.ui.components.statusColor
import com.nexuscore.mobile.ui.theme.NexusMono
import com.nexuscore.mobile.ui.components.StatusPill
import com.nexuscore.mobile.ui.components.formatNumber
import com.nexuscore.mobile.ui.components.formatPercent
import com.nexuscore.mobile.ui.components.formatUptime

@Composable
fun DashboardScreen(vm: NexusViewModel) {
    val metrics = vm.metrics
    val host = vm.selectedHost
    val critical = vm.alertEvents.count { it.status == "open" && it.severity == "crit" }
    val warning = vm.alertEvents.count { it.status == "open" && it.severity == "warn" }
    LazyColumn(
        contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            NexusCard {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text(
                                host?.name ?: "Host",
                                style = MaterialTheme.typography.titleLarge,
                                fontWeight = FontWeight.SemiBold,
                            )
                            Text(
                                host?.hostname ?: "—",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        StatusPill(
                            if (host?.online == true) "En línea" else "Sin latido",
                            if (host?.online == true) statusColor("ok") else statusColor("crit"),
                        )
                    }
                    if (critical > 0 || warning > 0) {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            if (critical > 0) StatusPill("$critical críticas", statusColor("crit"))
                            if (warning > 0) StatusPill("$warning avisos", statusColor("warn"))
                        }
                    }
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                        listOf(
                            "Uptime" to (metrics?.takeIf { it.uptimeSeconds > 0 }?.let { formatUptime(it.uptimeSeconds) } ?: "\u2014"),
                            "N\u00facleos" to (metrics?.cpuCount?.takeIf { it > 0 }?.toString() ?: "\u2014"),
                            "RAM" to (metrics?.let { formatPercent(it.ramPercent) } ?: "\u2014"),
                        ).forEach { (label, value) ->
                            Column(Modifier.weight(1f)) {
                                Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                Text(value, fontFamily = NexusMono, style = MaterialTheme.typography.bodyMedium)
                            }
                        }
                    }
                }
            }
        }
        if (metrics == null) {
            item { EmptyState("Cargando métricas…") }
        } else {
            item {
                NexusCard {
                    Column {
                        Row(
                            Modifier.fillMaxWidth().padding(16.dp, 12.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
                        ) {
                            Text("Sensores", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                            Text(
                                "${host?.name ?: "local"} · ${host?.os ?: "Windows"}",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        SensorRow(
                            name = "CPU",
                            value = formatPercent(metrics.cpuPercent),
                            progress = (metrics.cpuPercent / 100.0).toFloat(),
                            status = sensorStatus(metrics.cpuPercent, 80.0, 92.0),
                            threshold = "80 / 92 %",
                        )
                        SensorRow(
                            name = "RAM",
                            value = formatPercent(metrics.ramPercent),
                            progress = (metrics.ramPercent / 100.0).toFloat(),
                            status = sensorStatus(metrics.ramPercent, 80.0, 92.0),
                            threshold = "80 / 92 %",
                        )
                        SensorRow(
                            name = "Disco",
                            value = formatPercent(metrics.diskPercent),
                            progress = (metrics.diskPercent / 100.0).toFloat(),
                            status = sensorStatus(metrics.diskPercent, 80.0, 90.0),
                            threshold = "80 / 90 %",
                        )
                        SensorRow(
                            name = "Swap",
                            value = formatPercent(metrics.swapPercent),
                            progress = (metrics.swapPercent / 100.0).toFloat(),
                            status = sensorStatus(metrics.swapPercent, 25.0, 60.0),
                            threshold = "25 / 60 %",
                        )
                    }
                }
            }
            item {
                val cpuPoints = vm.history?.series?.get("cpu").orEmpty()
                NexusCard {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text("CPU · 60 min", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                            Text(
                                cpuPoints.mapNotNull { it.value }.maxOrNull()?.let { "máx ${formatNumber(it)}" } ?: "sin datos",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        LineChart(cpuPoints)
                    }
                }
            }
            item {
                NexusCard {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("Alarmas", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                        if (critical == 0 && warning == 0) {
                            Text(
                                "Sin alarmas abiertas en esta sesión.",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        } else {
                            vm.alertEvents.filter { it.status == "open" }.take(3).forEach { event ->
                                Row(
                                    Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                ) {
                                    Text(
                                        "${event.metric?.uppercase() ?: "ALERTA"} · ${event.value?.let { formatNumber(it) } ?: "—"}",
                                        style = MaterialTheme.typography.bodySmall,
                                        fontWeight = FontWeight.Medium,
                                    )
                                    StatusPill(
                                        if (event.severity == "crit") "Crítica" else "Aviso",
                                        statusColor(if (event.severity == "crit") "crit" else "warn"),
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

private fun sensorStatus(value: Double, warn: Double, crit: Double): String = when {
    value >= crit -> "crítico"
    value >= warn -> "aviso"
    else -> "correcto"
}

@Composable
fun ChartCard(points: List<MetricPoint>) {
    NexusCard {
        Column(Modifier.padding(14.dp)) {
            if (points.isEmpty()) {
                EmptyState("Sin histórico", "El host aún no ha enviado suficientes muestras.")
            } else {
                LineChart(points)
                Text(
                    "${points.size} muestras",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
fun HistoryScreen(vm: NexusViewModel) {
    val ranges = listOf("1h", "24h", "7d", "30d")
    val metrics = listOf("cpu", "ram", "swap", "disk", "load1", "temp")
    LazyColumn(
        contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            Row(
                Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                ranges.forEach { range ->
                    FilterChip(
                        selected = vm.historyRange == range,
                        onClick = { vm.changeHistoryRange(range) },
                        label = { Text(range) },
                    )
                }
            }
        }
        item {
            Row(
                Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                metrics.forEach { metric ->
                    FilterChip(
                        selected = vm.historyMetric == metric,
                        onClick = { vm.changeHistoryMetric(metric) },
                        label = { Text(metric) },
                    )
                }
            }
        }
        val series = vm.history?.series?.get(vm.historyMetric).orEmpty()
        item {
            NexusCard {
                Column(Modifier.padding(16.dp)) {
                    Text(
                        vm.historyMetric.uppercase(),
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Spacer(Modifier.height(8.dp))
                    LineChart(series)
                    Text(
                        "${series.size} muestras · periodo ${vm.history?.period ?: "—"}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
        item {
            NexusCard {
                Column(Modifier.padding(16.dp)) {
                    KeyValue("Rango", vm.history?.range ?: vm.historyRange)
                    KeyValue("Desde", vm.history?.fromTs?.toString() ?: "—")
                    KeyValue("Hasta", vm.history?.toTs?.toString() ?: "—")
                    KeyValue("Métricas", vm.history?.metrics?.joinToString(", ") ?: "—")
                }
            }
        }
    }
}

