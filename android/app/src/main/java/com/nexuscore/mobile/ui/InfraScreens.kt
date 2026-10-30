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
import androidx.compose.material3.AlertDialog
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.nexuscore.mobile.data.ContainerDto
import com.nexuscore.mobile.data.ServiceDto
import com.nexuscore.mobile.ui.components.EmptyState
import com.nexuscore.mobile.ui.components.KeyValue
import com.nexuscore.mobile.ui.components.NexusCard
import com.nexuscore.mobile.ui.components.SectionTitle
import com.nexuscore.mobile.ui.components.StatusPill
import com.nexuscore.mobile.ui.components.array
import com.nexuscore.mobile.ui.components.textOrNull
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonPrimitive

@Composable
fun ContainersScreen(vm: NexusViewModel) {
    var deploy by remember { mutableStateOf(false) }
    var pendingDelete by remember { mutableStateOf<ContainerDto?>(null) }
    var confirmPrune by remember { mutableStateOf(false) }
    LazyColumn(
        contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Button(onClick = { deploy = true }, modifier = Modifier.weight(1f)) {
                    Text("Desplegar")
                }
                OutlinedButton(onClick = { vm.refresh() }, modifier = Modifier.weight(1f)) {
                    Text("Refrescar")
                }
            }
        }
        item {
            OutlinedButton(
                onClick = { confirmPrune = true },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Limpiar recursos Docker") }
        }
        if (vm.containers.isEmpty()) {
            item { EmptyState("Sin contenedores", "El host no tiene contenedores o Docker no está disponible.") }
        }
        items(vm.containers, key = { it.id }) { container ->
            ContainerCard(
                container = container,
                onLogs = { vm.showContainerLogs(container) },
                onStats = { vm.showContainerStats(container) },
                onDelete = { pendingDelete = container },
            )
        }
    }
    if (deploy) {
        DeployContainerDialog(
            onDismiss = { deploy = false },
            onDeploy = { image, port, name ->
                vm.deployContainer(image, port, name)
                deploy = false
            },
        )
    }
    pendingDelete?.let { container ->
        AlertDialog(
            onDismissRequest = { pendingDelete = null },
            title = { Text("Eliminar contenedor") },
            text = { Text("¿Eliminar ${container.name}? Esta acción no se puede deshacer.") },
            confirmButton = {
                Button(onClick = {
                    vm.removeContainer(container)
                    pendingDelete = null
                }) { Text("Eliminar") }
            },
            dismissButton = {
                TextButton(onClick = { pendingDelete = null }) { Text("Cancelar") }
            },
        )
    }
    if (confirmPrune) {
        AlertDialog(
            onDismissRequest = { confirmPrune = false },
            title = { Text("Limpiar Docker") },
            text = { Text("Se eliminarán contenedores parados e imágenes sin usar. ¿Continuar?") },
            confirmButton = {
                Button(onClick = {
                    vm.pruneContainers()
                    confirmPrune = false
                }) { Text("Limpiar") }
            },
            dismissButton = {
                TextButton(onClick = { confirmPrune = false }) { Text("Cancelar") }
            },
        )
    }
}

@Composable
private fun ContainerCard(
    container: ContainerDto,
    onLogs: () -> Unit,
    onStats: () -> Unit,
    onDelete: () -> Unit,
) {
    NexusCard {
        Column(Modifier.padding(14.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        container.name,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        container.image,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                StatusPill(
                    container.state.ifBlank { container.status },
                    if (container.state == "running") MaterialTheme.colorScheme.tertiary
                    else MaterialTheme.colorScheme.outline,
                )
            }
            KeyValue("ID", container.shortId.ifBlank { container.id })
            KeyValue("Puertos", container.ports.joinToString(", ").ifBlank { "—" })
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextButton(onClick = onLogs) { Text("Logs") }
                TextButton(onClick = onStats) { Text("Stats") }
                TextButton(onClick = onDelete) { Text("Eliminar") }
            }
        }
    }
}

@Composable
private fun DeployContainerDialog(
    onDismiss: () -> Unit,
    onDeploy: (String, Int?, String?) -> Unit,
) {
    var image by remember { mutableStateOf("nginx") }
    var port by remember { mutableStateOf("") }
    var name by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Desplegar contenedor") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = image,
                    onValueChange = { image = it },
                    label = { Text("Imagen permitida") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = port,
                    onValueChange = { port = it },
                    label = { Text("Puerto host (opcional)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("Nombre (opcional)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            Button(
                onClick = { onDeploy(image, port.toIntOrNull(), name.ifBlank { null }) },
                enabled = image.isNotBlank(),
            ) { Text("Desplegar") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}

@Composable
fun ServicesScreen(vm: NexusViewModel) {
    val data = vm.services
    LazyColumn(
        contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Button(onClick = { vm.refresh() }, modifier = Modifier.weight(1f)) {
                    Text("Refrescar")
                }
            }
        }
        if (data == null) {
            item { EmptyState("Cargando servicios…") }
        } else if (!data.available) {
            item { EmptyState("systemd no disponible", data.reason ?: "Este host no expone systemd.") }
        } else if (data.services.isEmpty()) {
            item { EmptyState("Sin servicios") }
        } else {
            items(data.services, key = { it.name }) { service ->
                ServiceCard(service, vm)
            }
        }
    }
}

@Composable
private fun ServiceCard(service: ServiceDto, vm: NexusViewModel) {
    NexusCard {
        Column(Modifier.padding(14.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        service.name,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        service.description.ifBlank { service.friendlyName ?: "—" },
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                StatusPill(
                    service.activeState.ifBlank { service.loadState },
                    if (service.activeState == "active") MaterialTheme.colorScheme.tertiary
                    else MaterialTheme.colorScheme.error,
                )
            }
            KeyValue("Subestado", service.subState.ifBlank { "—" })
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                TextButton(onClick = { vm.serviceAction(service.name, "restart") }) {
                    Text("Reiniciar")
                }
                TextButton(onClick = { vm.serviceAction(service.name, "stop") }) {
                    Text("Parar")
                }
                TextButton(onClick = { vm.serviceAction(service.name, "start") }) {
                    Text("Iniciar")
                }
                TextButton(onClick = { vm.showServiceLogs(service.name) }) {
                    Text("Logs")
                }
            }
        }
    }
}

@Composable
fun NetworkScreen(vm: NexusViewModel) {
    var subnet by remember { mutableStateOf("") }
    var showChange by remember { mutableStateOf(false) }
    val network = vm.network
    LazyColumn(
        contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = subnet,
                    onValueChange = { subnet = it },
                    label = { Text("Subred (opcional)") },
                    placeholder = { Text("192.168.1.0/24") },
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                )
                Button(onClick = { vm.scanNetwork(subnet) }) {
                    Text("Escanear")
                }
            }
        }
        item {
            Button(
                onClick = { showChange = true },
                enabled = vm.selectedHostId == 1,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Cambiar IP del hub")
            }
            if (vm.selectedHostId != 1) {
                Text(
                    "El cambio de IP solo se aplica al hub local; en hosts remotos usa el panel web.",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        if (network == null) {
            item { EmptyState("Cargando red…") }
        } else if (!network.available) {
            item { EmptyState("Red no disponible", network.renderer) }
        } else if (network.interfaces.isEmpty()) {
            item { EmptyState("Sin interfaces") }
        } else {
            items(network.interfaces) { iface ->
                val name = iface.textOrNull("name") ?: "interfaz"
                val addresses = iface.array("addresses").joinToString(", ") {
                    (it as? JsonPrimitive)?.content ?: it.toString()
                }.ifBlank { "sin IP" }
                val up = iface.textOrNull("up") ?: "—"
                NexusCard {
                    Column(Modifier.padding(14.dp)) {
                        Row(
                            Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                        ) {
                            Text(name, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                            StatusPill(
                                if (up == "true") "up" else "down",
                                if (up == "true") MaterialTheme.colorScheme.tertiary else MaterialTheme.colorScheme.error,
                            )
                        }
                        KeyValue("Direcciones", addresses)
                        KeyValue("MTU", iface.textOrNull("mtu") ?: "—")
                        KeyValue("Velocidad", iface.textOrNull("speed_mbps")?.let { "$it Mbps" } ?: "—")
                        KeyValue("RX", iface.textOrNull("rx_bytes") ?: "—")
                        KeyValue("TX", iface.textOrNull("tx_bytes") ?: "—")
                    }
                }
            }
        }
    }
    if (showChange) {
        NetworkChangeDialog(vm) {
            vm.clearNetworkChange()
            showChange = false
        }
    }
}

@Composable
private fun NetworkChangeDialog(vm: NexusViewModel, onDismiss: () -> Unit) {
    val defaultIface = vm.network?.defaultInterface
        ?: vm.network?.interfaces?.firstOrNull()?.textOrNull("name")
        ?: "eth0"
    var interfaceName by remember { mutableStateOf(defaultIface) }
    var address by remember { mutableStateOf("") }
    var prefix by remember { mutableStateOf("24") }
    var gateway by remember { mutableStateOf(vm.network?.gateway ?: "") }
    var dns by remember { mutableStateOf("") }
    val prefixValue = prefix.toIntOrNull()
    val dnsList = dns.split(',', ' ').map { it.trim() }.filter { it.isNotBlank() }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Cambiar IP del hub") },
        text = {
            Column(
                Modifier
                    .heightIn(max = 500.dp)
                    .verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    "El cambio se aplica con watchdog: si no confirmas antes del TTL, la red vuelve sola. " +
                        "Puedes perder la conexión un momento.",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.error,
                )
                OutlinedTextField(
                    value = interfaceName,
                    onValueChange = { interfaceName = it },
                    label = { Text("Interfaz") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = address,
                    onValueChange = { address = it },
                    label = { Text("Nueva IPv4") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(
                        value = prefix,
                        onValueChange = { prefix = it },
                        label = { Text("Prefijo") },
                        singleLine = true,
                        modifier = Modifier.weight(0.5f),
                    )
                    OutlinedTextField(
                        value = gateway,
                        onValueChange = { gateway = it },
                        label = { Text("Puerta de enlace") },
                        singleLine = true,
                        modifier = Modifier.weight(1f),
                    )
                }
                OutlinedTextField(
                    value = dns,
                    onValueChange = { dns = it },
                    label = { Text("DNS separados por coma") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                val plan = vm.networkPlan
                if (plan != null) {
                    Text(plan.message, style = MaterialTheme.typography.bodySmall)
                    plan.warnings.forEach { Text("· $it", style = MaterialTheme.typography.labelSmall) }
                    Text(
                        plan.preview.take(1800),
                        fontFamily = FontFamily.Monospace,
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
                val applied = vm.networkApply
                if (applied != null) {
                    Text(applied.message, style = MaterialTheme.typography.bodySmall)
                    applied.warnings.forEach { Text("· $it", style = MaterialTheme.typography.labelSmall) }
                    Text(
                        "TTL: ${applied.ttl} s · token: ${applied.token ?: "—"}",
                        fontFamily = FontFamily.Monospace,
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
                if (vm.networkChangeError != null) {
                    Text(
                        vm.networkChangeError ?: "",
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
        },
        confirmButton = {
            if (vm.networkToken != null) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = {
                        vm.confirmNetwork()
                        onDismiss()
                    }) { Text("Confirmar") }
                    TextButton(onClick = {
                        vm.revertNetwork()
                        onDismiss()
                    }) { Text("Revertir") }
                }
            } else {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(
                        enabled = address.isNotBlank() && gateway.isNotBlank() && prefixValue != null,
                        onClick = {
                            vm.planNetwork(
                                interfaceName,
                                address,
                                prefixValue ?: 24,
                                gateway,
                                dnsList,
                            )
                        },
                    ) { Text("Planificar") }
                    Button(
                        enabled = address.isNotBlank() && gateway.isNotBlank() && prefixValue != null,
                        onClick = {
                            vm.applyNetwork(
                                interfaceName,
                                address,
                                prefixValue ?: 24,
                                gateway,
                                dnsList,
                            )
                        },
                    ) { Text("Aplicar") }
                }
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cerrar") } },
    )
}

@Composable
fun SystemScreen(vm: NexusViewModel) {
    LazyColumn(
        contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item { SectionTitle("Puertos en escucha", vm.ports?.count?.toString()) }
        val ports = vm.ports
        if (ports == null) {
            item { EmptyState("Cargando puertos…") }
        } else if (!ports.available) {
            item { EmptyState("No disponible", ports.reason ?: "—") }
        } else {
            items(ports.ports.take(40)) { port ->
                NexusCard {
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .padding(12.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text(
                                "${port.ip}:${port.port}",
                                fontFamily = FontFamily.Monospace,
                                style = MaterialTheme.typography.bodySmall,
                            )
                            Text(
                                port.process ?: "—",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        StatusPill(port.status, MaterialTheme.colorScheme.secondary)
                    }
                }
            }
        }
        item { SectionTitle("Actualizaciones", vm.updates?.count?.toString()) }
        val updates = vm.updates
        if (updates == null) {
            item { EmptyState("Cargando actualizaciones…") }
        } else if (!updates.available) {
            item { EmptyState("No disponible", updates.reason ?: "—") }
        } else {
            item {
                NexusCard {
                    Column(Modifier.padding(14.dp)) {
                        KeyValue("Pendientes", updates.count.toString())
                        updates.packages.take(10).forEach { item ->
                            Text(
                                item.toString().trim('"'),
                                fontFamily = FontFamily.Monospace,
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                    }
                }
            }
        }
        item { SectionTitle("Discos SMART", vm.smart?.count?.toString()) }
        val smart = vm.smart
        if (smart == null) {
            item { EmptyState("Cargando SMART…") }
        } else if (!smart.available) {
            item { EmptyState("No disponible", smart.reason ?: "—") }
        } else {
            items(smart.disks) { disk ->
                NexusCard {
                    Text(
                        disk.toString(),
                        modifier = Modifier.padding(14.dp),
                        fontFamily = FontFamily.Monospace,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
        }
    }
}
