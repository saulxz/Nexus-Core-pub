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
import com.nexuscore.mobile.ui.components.StatusPill
import com.nexuscore.mobile.ui.components.formatNumber
import com.nexuscore.mobile.ui.components.formatPercent
import com.nexuscore.mobile.ui.components.formatUptime

@Composable
fun DashboardScreen(vm: NexusViewModel) {
    val metrics = vm.metrics
    LazyColumn(
        contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            NexusCard {
                Column(Modifier.padding(16.dp)) {
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Column {
                            Text(
                                vm.selectedHost?.name ?: "Host",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.SemiBold,
                            )
                            Text(
                                vm.selectedHost?.hostname ?: "—",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        val online = vm.selectedHost?.online == true
                        StatusPill(
                            if (online) "online" else "offline",
                            if (online) MaterialTheme.colorScheme.tertiary else MaterialTheme.colorScheme.error,
                        )
                    }
                    Spacer(Modifier.height(10.dp))
                    KeyValue("Sistema", vm.selectedHost?.os ?: "—")
                    KeyValue("Agente", vm.selectedHost?.agentVersion ?: "—")
                    KeyValue("Uptime", metrics?.takeIf { it.uptimeSeconds > 0 }?.let { formatUptime(it.uptimeSeconds) } ?: "—")
                }
            }
        }
        if (metrics == null) {
            item { EmptyState("Cargando métricas…") }
        } else {
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    MetricCard(
                        label = "CPU",
                        value = formatPercent(metrics.cpuPercent),
                        detail = if (metrics.cpuCount > 0) "${metrics.cpuCount} núcleos" else "—",
                        modifier = Modifier.weight(1f),
                    )
                    MetricCard(
                        label = "RAM",
                        value = formatPercent(metrics.ramPercent),
                        detail = if (metrics.ramTotalMb > 0) "${formatNumber(metrics.ramUsedMb)} / ${formatNumber(metrics.ramTotalMb)} MB" else "—",
                        modifier = Modifier.weight(1f),
                    )
                }
            }
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    MetricCard(
                        label = "Disco",
                        value = formatPercent(metrics.diskPercent),
                        modifier = Modifier.weight(1f),
                    )
                    MetricCard(
                        label = "Swap",
                        value = formatPercent(metrics.swapPercent),
                        modifier = Modifier.weight(1f),
                    )
                }
            }
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    MetricCard(
                        label = "Carga 1m",
                        value = formatNumber(metrics.loadAvg.firstOrNull() ?: 0.0),
                        modifier = Modifier.weight(1f),
                    )
                    MetricCard(
                        label = "Temperatura",
                        value = metrics.cpuTempC?.let { "%.1f °C".format(it) } ?: "—",
                        modifier = Modifier.weight(1f),
                    )
                }
            }
            item { SectionTitle("CPU · últimos 60 minutos", "1h") }
            item { ChartCard(vm.history?.series?.get("cpu").orEmpty()) }
            item { SectionTitle("RAM · últimos 60 minutos", "1h") }
            item { ChartCard(vm.history?.series?.get("ram").orEmpty()) }
        }
    }
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
