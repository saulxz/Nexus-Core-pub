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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.nexuscore.mobile.data.HostDto
import com.nexuscore.mobile.ui.components.EmptyState
import com.nexuscore.mobile.ui.components.KeyValue
import com.nexuscore.mobile.ui.components.NexusCard
import com.nexuscore.mobile.ui.components.StatusPill
import com.nexuscore.mobile.ui.components.formatTimestamp

@Composable
fun HostsScreen(vm: NexusViewModel) {
    var editing by remember { mutableStateOf<HostDto?>(null) }
    LazyColumn(
        contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Button(onClick = { vm.showEnrollment() }, modifier = Modifier.weight(1f)) {
                    Text("Añadir host")
                }
                OutlinedButton(onClick = { vm.refresh() }, modifier = Modifier.weight(1f)) {
                    Text("Refrescar")
                }
            }
        }
        if (vm.hosts.isNotEmpty()) {
            item {
                Text(
                    "${vm.hosts.size} hosts · ${vm.hosts.count { it.online }} online",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        if (vm.hosts.isEmpty()) {
            item { EmptyState("Sin hosts", "Enrola un agente desde el panel o pega el token de alta.") }
        }
        items(
            vm.hosts.sortedWith(compareByDescending<HostDto> { it.online }.thenBy { it.id }),
            key = { it.id },
        ) { host ->
            HostCard(
                host = host,
                selected = host.id == vm.selectedHostId,
                onSelect = { vm.selectHost(host.id) },
                onEdit = { editing = host },
                onDelete = { vm.deleteHost(host) },
            )
        }
    }
    editing?.let { host ->
        EditHostDialog(
            host = host,
            onDismiss = { editing = null },
            onSave = { name, notes ->
                vm.updateHost(host, name, notes)
                editing = null
            },
        )
    }
}

@Composable
private fun HostCard(
    host: HostDto,
    selected: Boolean,
    onSelect: () -> Unit,
    onEdit: () -> Unit,
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
                        host.name,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        host.hostname ?: "—",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                StatusPill(
                    if (host.online) "online" else host.status,
                    if (host.online) MaterialTheme.colorScheme.tertiary else MaterialTheme.colorScheme.error,
                )
            }
            Spacer(Modifier.height(8.dp))
            KeyValue("Sistema", host.os ?: "—")
            KeyValue("Agente", host.agentVersion ?: "—")
            KeyValue("Último latido", host.secondsSinceSeen?.let { "hace ${it.toInt()} s" } ?: "—")
            KeyValue("Enrolado", formatTimestamp(host.enrolledAt))
            if (!host.notes.isNullOrBlank()) KeyValue("Notas", host.notes)
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (!selected) {
                    OutlinedButton(onClick = onSelect) { Text("Seleccionar") }
                } else {
                    Text(
                        "Seleccionado",
                        color = MaterialTheme.colorScheme.primary,
                        style = MaterialTheme.typography.labelMedium,
                    )
                }
                TextButton(onClick = onEdit) { Text("Editar") }
                if (host.isLocal == 0) {
                    TextButton(onClick = onDelete) { Text("Eliminar") }
                }
            }
        }
    }
}

@Composable
private fun EditHostDialog(
    host: HostDto,
    onDismiss: () -> Unit,
    onSave: (String, String) -> Unit,
) {
    var name by remember(host.id) { mutableStateOf(host.name) }
    var notes by remember(host.id) { mutableStateOf(host.notes.orEmpty()) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Editar host") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("Nombre") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = notes,
                    onValueChange = { notes = it },
                    label = { Text("Notas") },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            Button(onClick = { onSave(name, notes) }, enabled = name.isNotBlank()) {
                Text("Guardar")
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}
