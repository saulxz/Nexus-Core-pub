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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.heightIn
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.nexuscore.mobile.data.AuditEntryDto
import com.nexuscore.mobile.data.FindingDto
import com.nexuscore.mobile.data.SessionDto
import com.nexuscore.mobile.data.TokenDto
import com.nexuscore.mobile.ui.components.EmptyState
import com.nexuscore.mobile.ui.components.KeyValue
import com.nexuscore.mobile.ui.components.NexusCard
import com.nexuscore.mobile.ui.components.SectionTitle
import com.nexuscore.mobile.ui.components.StatusPill
import com.nexuscore.mobile.ui.components.formatTimestamp

@Composable
fun AuditScreen(vm: NexusViewModel) {
    var tab by remember { mutableIntStateOf(0) }
    Column(Modifier.fillMaxWidth()) {
        TabRow(selectedTabIndex = tab) {
            Tab(selected = tab == 0, onClick = { tab = 0 }, text = { Text("Seguridad") })
            Tab(selected = tab == 1, onClick = { tab = 1 }, text = { Text("Registro") })
        }
        LazyColumn(
            contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            if (tab == 0) {
                val audit = vm.securityAudit
                item {
                    if (audit == null) {
                        EmptyState("Cargando auditoría…")
                    } else {
                        NexusCard {
                            Column(Modifier.padding(16.dp)) {
                                Row(
                                    Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                ) {
                                    Column {
                                        Text("Auditoría de seguridad", style = MaterialTheme.typography.titleMedium)
                                        Text(
                                            "${audit.host} · ${audit.platform}",
                                            style = MaterialTheme.typography.bodySmall,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        )
                                    }
                                    Text(
                                        "${audit.score}",
                                        style = MaterialTheme.typography.headlineLarge,
                                        fontWeight = FontWeight.Bold,
                                        color = if (audit.score >= 80) MaterialTheme.colorScheme.tertiary
                                        else MaterialTheme.colorScheme.secondary,
                                    )
                                }
                                Text(
                                    "Críticas ${audit.counts["critical"] ?: 0} · Altas ${audit.counts["high"] ?: 0} · " +
                                        "Medias ${audit.counts["medium"] ?: 0} · Bajas ${audit.counts["low"] ?: 0}",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.padding(top = 8.dp),
                                )
                                Text(
                                    "${audit.findings.size} hallazgos · ${audit.durationMs} ms" +
                                        if (audit.cached) " · caché" else "",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    Button(onClick = { vm.runSecurityAudit() }) { Text("Ejecutar") }
                                    OutlinedButton(onClick = { vm.saveBaseline() }) { Text("Baseline") }
                                    TextButton(onClick = { vm.clearBaseline() }) { Text("Borrar") }
                                }
                            }
                        }
                    }
                }
                if (audit != null) {
                    items(
                        audit.findings.sortedBy {
                            when (it.severity) {
                                "critical" -> 0
                                "high" -> 1
                                "medium" -> 2
                                "low" -> 3
                                else -> 4
                            }
                        },
                        key = { it.id },
                    ) { finding ->
                        FindingCard(finding)
                    }
                }
            } else {
                if (vm.auditEntries.isEmpty()) {
                    item { EmptyState("Sin eventos de auditoría") }
                }
                items(vm.auditEntries, key = { "${it.seq}-${it.hash}" }) { entry ->
                    AuditEntryCard(entry)
                }
            }
        }
    }
}

@Composable
private fun FindingCard(finding: FindingDto) {
    val color = when (finding.severity) {
        "critical" -> MaterialTheme.colorScheme.error
        "high" -> MaterialTheme.colorScheme.error
        "medium" -> MaterialTheme.colorScheme.secondary
        "low" -> MaterialTheme.colorScheme.primary
        else -> MaterialTheme.colorScheme.outline
    }
    NexusCard {
        Column(Modifier.padding(14.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(
                    "${finding.id} · ${finding.title}",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.weight(1f),
                )
                StatusPill(
                    "${finding.severity}/${finding.status}",
                    if (finding.status == "pass") MaterialTheme.colorScheme.tertiary else color,
                )
            }
            Text(
                finding.evidence,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 6.dp),
            )
            if (finding.status != "pass") {
                Text(
                    finding.recommendation,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier.padding(top = 6.dp),
                )
            }
        }
    }
}

@Composable
private fun AuditEntryCard(entry: AuditEntryDto) {
    NexusCard {
        Column(Modifier.padding(12.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(
                    entry.action,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    entry.result,
                    style = MaterialTheme.typography.labelSmall,
                    color = if (entry.result == "ok") MaterialTheme.colorScheme.tertiary
                    else MaterialTheme.colorScheme.error,
                )
            }
            Text(
                "${entry.actor} → ${entry.target}",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                formatTimestamp(entry.ts),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.outline,
            )
        }
    }
}

@Composable
fun SecurityScreen(vm: NexusViewModel) {
    var showTokenDialog by remember { mutableStateOf(false) }
    var confirmSetup by remember { mutableStateOf(false) }
    var showDisable by remember { mutableStateOf(false) }
    LazyColumn(
        contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            val twoFactor = vm.twoFactor
            NexusCard {
                Column(Modifier.padding(16.dp)) {
                    Text("Segundo factor", style = MaterialTheme.typography.titleMedium)
                    if (twoFactor == null) {
                        Text("Cargando…", style = MaterialTheme.typography.bodySmall)
                    } else {
                        KeyValue("Estado", if (twoFactor.enabled) "Activo" else "Desactivado")
                        KeyValue("Códigos de recuperación", twoFactor.recoveryLeft.toString())
                    }
                    Text(
                        "El 2FA se puede configurar aquí y se usa al iniciar sesión.",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = { confirmSetup = true }) {
                            Text(if (twoFactor?.enabled == true) "Reconfigurar 2FA" else "Activar 2FA")
                        }
                        if (twoFactor?.enabled == true) {
                            OutlinedButton(onClick = { showDisable = true }) { Text("Desactivar") }
                        }
                    }
                }
            }
        }
        item {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Button(onClick = { vm.refresh() }, modifier = Modifier.weight(1f)) {
                    Text("Refrescar")
                }
                OutlinedButton(onClick = { showTokenDialog = true }, modifier = Modifier.weight(1f)) {
                    Text("Crear token")
                }
            }
        }
        item { SectionTitle("Sesiones abiertas", vm.sessions?.count?.toString()) }
        val sessions = vm.sessions
        if (sessions == null) {
            item { EmptyState("Cargando sesiones…") }
        } else {
            items(sessions.sessions, key = { it.id }) { session ->
                SessionCard(session, vm)
            }
        }
        item { SectionTitle("Tokens de API", vm.tokens?.count?.toString()) }
        val tokens = vm.tokens
        if (tokens == null) {
            item { EmptyState("Cargando tokens…") }
        } else if (tokens.tokens.isEmpty()) {
            item { EmptyState("Sin tokens") }
        } else {
            items(tokens.tokens, key = { it.id }) { token ->
                TokenCard(token, vm)
            }
        }
    }
    if (showTokenDialog) {
        TokenDialog(
            onDismiss = { showTokenDialog = false },
            onCreate = { name, scope ->
                vm.createToken(name, scope)
                showTokenDialog = false
            },
        )
    }
    if (confirmSetup) {
        AlertDialog(
            onDismissRequest = { confirmSetup = false },
            title = { Text("Reconfigurar 2FA") },
            text = {
                Text(
                    "Se generará un secreto nuevo y el anterior dejará de valer. " +
                        "Añádelo a Google Authenticator y confirma antes de cerrar sesión.",
                )
            },
            confirmButton = {
                Button(onClick = {
                    confirmSetup = false
                    vm.startTwoFactorSetup()
                }) { Text("Continuar") }
            },
            dismissButton = {
                TextButton(onClick = { confirmSetup = false }) { Text("Cancelar") }
            },
        )
    }
    val setup = vm.twoFactorSetup
    if (setup != null) {
        var code by remember(setup.secret) { mutableStateOf("") }
        AlertDialog(
            onDismissRequest = { },
            title = { Text("Activar 2FA") },
            text = {
                Column(
                    Modifier
                        .heightIn(max = 460.dp)
                        .verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text("Añade este secreto a Google Authenticator:", style = MaterialTheme.typography.bodySmall)
                    SelectionContainer {
                        Text(
                            setup.secret,
                            fontFamily = FontFamily.Monospace,
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                    Text("URI otpauth:", style = MaterialTheme.typography.labelSmall)
                    SelectionContainer {
                        Text(
                            setup.otpauthUrl,
                            fontFamily = FontFamily.Monospace,
                            style = MaterialTheme.typography.labelSmall,
                        )
                    }
                    Text("Códigos de recuperación (se muestran una vez):", style = MaterialTheme.typography.labelSmall)
                    SelectionContainer {
                        Text(
                            setup.recoveryCodes.joinToString("\n"),
                            fontFamily = FontFamily.Monospace,
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                    OutlinedTextField(
                        value = code,
                        onValueChange = { code = it.filter(Char::isDigit).take(6) },
                        label = { Text("Código de 6 dígitos") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Text(
                        "Si cierras sin activar, el 2FA anterior habrá dejado de valer.",
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
            },
            confirmButton = {
                Button(
                    onClick = { vm.enableTwoFactor(code) },
                    enabled = code.length == 6,
                ) { Text("Activar") }
            },
            dismissButton = {
                TextButton(onClick = { vm.clearTwoFactorSetup() }) { Text("Cerrar sin activar") }
            },
        )
    }
    if (showDisable) {
        var password by remember { mutableStateOf("") }
        AlertDialog(
            onDismissRequest = { showDisable = false },
            title = { Text("Desactivar 2FA") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Introduce la contraseña actual. La cuenta quedará solo con contraseña.")
                    OutlinedTextField(
                        value = password,
                        onValueChange = { password = it },
                        label = { Text("Contraseña") },
                        singleLine = true,
                        visualTransformation = androidx.compose.ui.text.input.PasswordVisualTransformation(),
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            },
            confirmButton = {
                Button(
                    onClick = {
                        vm.disableTwoFactor(password)
                        showDisable = false
                    },
                    enabled = password.isNotBlank(),
                ) { Text("Desactivar") }
            },
            dismissButton = {
                TextButton(onClick = { showDisable = false }) { Text("Cancelar") }
            },
        )
    }
}

@Composable
private fun SessionCard(session: SessionDto, vm: NexusViewModel) {
    NexusCard {
        Column(Modifier.padding(12.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(
                    session.username,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                )
                StatusPill(
                    if (session.isCurrent) "actual" else if (session.expired) "expirada" else "abierta",
                    if (session.isCurrent) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.secondary,
                )
            }
            KeyValue("Creada", formatTimestamp(java.time.Instant.ofEpochSecond(session.createdAt.toLong()).toString()))
            KeyValue("Inactiva", "${session.idleSeconds.toInt()} s")
            KeyValue("Caduca", formatTimestamp(java.time.Instant.ofEpochSecond(session.expiresAt.toLong()).toString()))
            if (!session.isCurrent) {
                TextButton(onClick = { vm.revokeSession(session.id) }) { Text("Revocar") }
            }
        }
    }
}

@Composable
private fun TokenCard(token: TokenDto, vm: NexusViewModel) {
    NexusCard {
        Column(Modifier.padding(12.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(
                    token.name,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                )
                StatusPill(
                    if (token.active) "activo" else "revocado",
                    if (token.active) MaterialTheme.colorScheme.tertiary else MaterialTheme.colorScheme.outline,
                )
            }
            KeyValue("Ámbito", token.scope)
            KeyValue("Prefijo", token.prefix)
            KeyValue("Último uso", token.lastUsedAt ?: "—")
            if (token.active) {
                TextButton(onClick = { vm.revokeToken(token.id) }) { Text("Revocar") }
            }
        }
    }
}

@Composable
private fun TokenDialog(
    onDismiss: () -> Unit,
    onCreate: (String, String) -> Unit,
) {
    var name by remember { mutableStateOf("") }
    var scope by remember { mutableStateOf("read") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Crear token de API") },
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
                    OutlinedButton(onClick = { scope = "read" }) { Text("read") }
                    OutlinedButton(onClick = { scope = "write" }) { Text("write") }
                }
                Text(
                    "El token se mostrará una sola vez. No uses write en el móvil.",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        confirmButton = {
            Button(onClick = { onCreate(name, scope) }, enabled = name.isNotBlank()) {
                Text("Crear")
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}
