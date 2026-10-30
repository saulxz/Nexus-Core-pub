package com.nexuscore.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.heightIn
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.FilterChip
import com.nexuscore.mobile.data.AiSettingsDto
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.nexuscore.mobile.data.ChatLine
import com.nexuscore.mobile.ui.components.EmptyState
import com.nexuscore.mobile.ui.components.KeyValue
import com.nexuscore.mobile.ui.components.NexusCard
import com.nexuscore.mobile.ui.components.SectionTitle
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString

private val ANSI = Regex("\u001B\\[[0-?]*[ -/]*[@-~]")

@Composable
fun TerminalScreen(vm: NexusViewModel) {
    var target by remember { mutableStateOf(vm.terminalTargets.firstOrNull()) }
    var menu by remember { mutableStateOf(false) }
    var output by remember { mutableStateOf("") }
    var input by remember { mutableStateOf("") }
    var socket by remember { mutableStateOf<WebSocket?>(null) }
    var connected by remember { mutableStateOf(false) }
    val scroll = rememberScrollState()

    fun connect() {
        val selected = target ?: return
        socket?.close(1000, "reconnect")
        output = ""
        connected = false
        val listener = object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                connected = true
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                output += ANSI.replace(text, "")
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                output += ANSI.replace(bytes.utf8(), "")
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                connected = false
                output += "\r\n[Error: ${t.message}]\r\n"
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                connected = false
                output += "\r\n[Terminal cerrada]\r\n"
            }
        }
        socket = vm.openTerminal(selected.id, listener)
    }

    DisposableEffect(Unit) {
        onDispose { socket?.close(1000, "salir") }
    }

    Column(
        Modifier
            .fillMaxSize()
            .padding(12.dp),
    ) {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Box(Modifier.weight(1f)) {
                OutlinedButton(onClick = { menu = true }, modifier = Modifier.fillMaxWidth()) {
                    Text(target?.let { "${it.name}${if (it.available) "" else " (no disponible)"}" } ?: "Elegir host")
                }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    vm.terminalTargets.forEach { item ->
                        DropdownMenuItem(
                            text = { Text(item.name) },
                            onClick = {
                                target = item
                                menu = false
                            },
                        )
                    }
                }
            }
            Button(onClick = { connect() }, enabled = target?.available == true && !connected) {
                Text(if (connected) "Conectada" else "Conectar")
            }
            OutlinedButton(
                onClick = {
                    socket?.close(1000, "cerrar")
                    socket = null
                    connected = false
                },
                enabled = connected,
            ) { Text("Cerrar") }
        }
        if (target != null && !target!!.available) {
            Text(
                target!!.reason ?: "Terminal no disponible",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.padding(vertical = 8.dp),
            )
        }
        Surface(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth()
                .padding(vertical = 8.dp),
            color = MaterialTheme.colorScheme.background,
            shape = androidx.compose.foundation.shape.RoundedCornerShape(12.dp),
        ) {
            Column(
                Modifier
                    .fillMaxSize()
                    .padding(10.dp)
                    .verticalScroll(scroll),
            ) {
                SelectionContainer {
                    Text(
                        output.ifBlank { "Pulsa Conectar para abrir la sesión SSH." },
                        fontFamily = FontFamily.Monospace,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
        }
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedTextField(
                value = input,
                onValueChange = { input = it },
                placeholder = { Text("Escribe un comando") },
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
            Button(
                onClick = {
                    socket?.send(input + "\r")
                    input = ""
                },
                enabled = connected && input.isNotBlank(),
            ) { Text("Enviar") }
        }
    }
    LaunchedEffect(output) {
        scroll.animateScrollTo(scroll.maxValue)
    }
}

@Composable
fun AssistantScreen(vm: NexusViewModel) {
    var input by remember { mutableStateOf("") }
    val listState = rememberLazyListState()
    LaunchedEffect(vm.chatLines.size) {
        if (vm.chatLines.isNotEmpty()) listState.animateScrollToItem(vm.chatLines.lastIndex)
    }
    Column(
        Modifier
            .fillMaxSize()
            .padding(12.dp),
    ) {
        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (vm.chatLines.isEmpty()) {
                item {
                    EmptyState(
                        "Asistente Nexus",
                        "Pregunta por el estado de la flota. Las acciones sensibles requieren confirmación.",
                    )
                }
            }
            items(vm.chatLines) { line ->
                ChatBubble(line)
            }
            items(vm.chatPending) { pending ->
                PendingActionCard(pending, vm)
            }
        }
        if (vm.chatError != null) {
            Text(
                vm.chatError ?: "",
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodySmall,
            )
        }
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedTextField(
                value = input,
                onValueChange = { input = it },
                placeholder = { Text("Escribe al asistente") },
                modifier = Modifier.weight(1f),
                maxLines = 3,
            )
            Button(
                onClick = {
                    vm.sendChat(input)
                    input = ""
                },
                enabled = input.isNotBlank() && !vm.chatSending,
            ) { Text(if (vm.chatSending) "…" else "Enviar") }
        }
    }
}

@Composable
private fun ChatBubble(line: ChatLine) {
    val isUser = line.role == "user"
    Surface(
        color = if (isUser) MaterialTheme.colorScheme.primary.copy(alpha = 0.18f)
        else MaterialTheme.colorScheme.surface,
        shape = androidx.compose.foundation.shape.RoundedCornerShape(14.dp),
        modifier = Modifier.fillMaxWidth(if (isUser) 0.9f else 1f),
    ) {
        Text(
            line.content,
            modifier = Modifier.padding(12.dp),
            style = MaterialTheme.typography.bodyMedium,
        )
    }
}

@Composable
private fun PendingActionCard(element: kotlinx.serialization.json.JsonElement, vm: NexusViewModel) {
    val obj = runCatching { element.jsonObject }.getOrNull() ?: return
    val id = obj["id"]?.jsonPrimitive?.content ?: return
    val summary = obj["summary"]?.jsonPrimitive?.content ?: "Acción pendiente"
    val tool = obj["tool"]?.jsonPrimitive?.content ?: "—"
    NexusCard {
        Column(Modifier.padding(12.dp)) {
            Text("Propuesta de acción", style = MaterialTheme.typography.labelMedium)
            Text(summary, style = MaterialTheme.typography.bodyMedium)
            Text("Herramienta: $tool", style = MaterialTheme.typography.labelSmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { vm.confirmPending(id, true) }) { Text("Confirmar") }
                TextButton(onClick = { vm.confirmPending(id, false) }) { Text("Cancelar") }
            }
        }
    }
}

@Composable
fun SettingsScreen(vm: NexusViewModel) {
    var showAiEditor by remember { mutableStateOf(false) }
    LazyColumn(
        contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            NexusCard {
                Column(Modifier.padding(16.dp)) {
                    Text("Conexión", style = MaterialTheme.typography.titleMedium)
                    KeyValue("Hub", vm.baseUrl)
                    KeyValue("Usuario", vm.username.ifBlank { "admin" })
                    KeyValue("HTTPS", "obligatorio")
                }
            }
        }
        item {
            NexusCard {
                Column(Modifier.padding(16.dp)) {
                    Text("Notificaciones", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "La app revisa alertas en segundo plano cada 15 minutos. No usa FCM.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
        item {
            NexusCard {
                Column(Modifier.padding(16.dp)) {
                    Text("Asistente de IA", style = MaterialTheme.typography.titleMedium)
                    val ai = vm.aiSettings
                    if (ai == null) {
                        Text("Cargando configuración…", style = MaterialTheme.typography.bodySmall)
                    } else {
                        KeyValue("Proveedor", ai.provider.ifBlank { "sin configurar" })
                        KeyValue("Modelo", ai.model.ifBlank { "—" })
                        KeyValue("Temperatura", ai.temperature.toString())
                        val gemini = ai.keys["gemini"]
                        if (gemini?.configured == true) {
                            KeyValue("Clave Gemini", gemini.masked)
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = { showAiEditor = true }, enabled = vm.aiSettings != null) {
                            Text("Editar IA")
                        }
                        OutlinedButton(onClick = { vm.testAi() }, enabled = vm.aiSettings != null) {
                            Text("Probar")
                        }
                    }
                    val test = vm.aiTest
                    if (test != null) {
                        Text(
                            "${if (test.ok) "OK" else "Error"} · ${test.provider}/${test.model} · ${test.latencyMs ?: "—"} ms",
                            style = MaterialTheme.typography.bodySmall,
                            color = if (test.ok) MaterialTheme.colorScheme.tertiary else MaterialTheme.colorScheme.error,
                        )
                        Text(test.detail, style = MaterialTheme.typography.labelSmall)
                    }
                }
            }
        }
        item {
            OutlinedButton(
                onClick = { vm.logout() },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Cerrar sesión") }
        }
        item {
            Text(
                "Nexus-Core Android 1.0.0 · interfaz nativa Jetpack Compose",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.outline,
            )
        }
    }
    if (showAiEditor) {
        AiEditorDialog(
            settings = vm.aiSettings,
            onDismiss = { showAiEditor = false },
            onSave = { provider, model, temperature, openaiUrl, geminiUrl, ollamaUrl, openaiKey, anthropicKey, geminiKey ->
                vm.saveAiSettings(
                    provider,
                    model,
                    temperature,
                    openaiUrl,
                    geminiUrl,
                    ollamaUrl,
                    openaiKey,
                    anthropicKey,
                    geminiKey,
                )
                showAiEditor = false
            },
        )
    }
}

@Composable
private fun AiEditorDialog(
    settings: AiSettingsDto?,
    onDismiss: () -> Unit,
    onSave: (String, String, Double, String, String, String, String, String, String) -> Unit,
) {
    var provider by remember(settings?.provider) { mutableStateOf(settings?.provider ?: "gemini") }
    var model by remember(settings?.model) { mutableStateOf(settings?.model ?: "gemini-2.5-flash") }
    var temperature by remember(settings?.temperature) { mutableStateOf((settings?.temperature ?: 0.2).toString()) }
    var openaiUrl by remember(settings?.openaiBaseUrl) { mutableStateOf(settings?.openaiBaseUrl ?: "") }
    var geminiUrl by remember(settings?.geminiBaseUrl) { mutableStateOf(settings?.geminiBaseUrl ?: "") }
    var ollamaUrl by remember(settings?.ollamaBaseUrl) { mutableStateOf(settings?.ollamaBaseUrl ?: "") }
    var openaiKey by remember { mutableStateOf("") }
    var anthropicKey by remember { mutableStateOf("") }
    var geminiKey by remember { mutableStateOf("") }
    val tempValue = temperature.toDoubleOrNull()
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Configuración de IA") },
        text = {
            Column(
                Modifier
                    .heightIn(max = 520.dp)
                    .verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text("Proveedor", style = MaterialTheme.typography.labelSmall)
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf("openai", "anthropic", "gemini", "ollama").forEach { value ->
                        FilterChip(
                            selected = provider == value,
                            onClick = { provider = value },
                            label = { Text(value) },
                        )
                    }
                }
                OutlinedTextField(
                    value = model,
                    onValueChange = { model = it },
                    label = { Text("Modelo") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = temperature,
                    onValueChange = { temperature = it },
                    label = { Text("Temperatura (0-2)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = openaiUrl,
                    onValueChange = { openaiUrl = it },
                    label = { Text("Base URL OpenAI") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = geminiUrl,
                    onValueChange = { geminiUrl = it },
                    label = { Text("Base URL Gemini") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = ollamaUrl,
                    onValueChange = { ollamaUrl = it },
                    label = { Text("Base URL Ollama") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Text(
                    "Claves nuevas (deja en blanco para conservar la actual):",
                    style = MaterialTheme.typography.labelSmall,
                )
                OutlinedTextField(
                    value = openaiKey,
                    onValueChange = { openaiKey = it },
                    label = { Text("OpenAI API key") },
                    singleLine = true,
                    visualTransformation = androidx.compose.ui.text.input.PasswordVisualTransformation(),
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = anthropicKey,
                    onValueChange = { anthropicKey = it },
                    label = { Text("Anthropic API key") },
                    singleLine = true,
                    visualTransformation = androidx.compose.ui.text.input.PasswordVisualTransformation(),
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = geminiKey,
                    onValueChange = { geminiKey = it },
                    label = { Text("Gemini API key") },
                    singleLine = true,
                    visualTransformation = androidx.compose.ui.text.input.PasswordVisualTransformation(),
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    onSave(
                        provider,
                        model,
                        tempValue ?: 0.2,
                        openaiUrl,
                        geminiUrl,
                        ollamaUrl,
                        openaiKey,
                        anthropicKey,
                        geminiKey,
                    )
                },
                enabled = model.isNotBlank() && tempValue != null && tempValue in 0.0..2.0,
            ) { Text("Guardar") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}
