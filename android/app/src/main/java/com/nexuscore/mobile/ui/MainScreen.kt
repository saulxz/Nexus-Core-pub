package com.nexuscore.mobile.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Logout
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationDrawerItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.nexuscore.mobile.ui.components.ErrorCard
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(vm: NexusViewModel) {
    val drawerState = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    var showDrawerMenu by remember { mutableStateOf(false) }

    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            ModalDrawerSheet {
                Column(
                    Modifier
                        .fillMaxSize()
                        .verticalScroll(rememberScrollState())
                        .padding(12.dp),
                ) {
                    Text(
                        "Nexus-Core",
                        style = MaterialTheme.typography.headlineSmall,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(12.dp),
                    )
                    Text(
                        vm.username.ifBlank { "admin" },
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = 12.dp),
                    )
                    HorizontalDivider(Modifier.padding(vertical = 10.dp))
                    Section.entries.forEach { section ->
                        NavigationDrawerItem(
                            label = { Text(section.label) },
                            icon = { Icon(section.icon, contentDescription = null) },
                            selected = vm.section == section,
                            onClick = {
                                vm.selectSection(section)
                                scope.launch { drawerState.close() }
                            },
                            modifier = Modifier.padding(vertical = 1.dp),
                        )
                    }
                    HorizontalDivider(Modifier.padding(vertical = 10.dp))
                    NavigationDrawerItem(
                        label = { Text("Cerrar sesión") },
                        icon = { Icon(Icons.Filled.Logout, contentDescription = null) },
                        selected = false,
                        onClick = {
                            scope.launch { drawerState.close() }
                            vm.logout()
                        },
                    )
                }
            }
        },
    ) {
        Scaffold(
            topBar = {
                TopAppBar(
                    title = { Text(vm.section.label) },
                    navigationIcon = {
                        IconButton(onClick = { scope.launch { drawerState.open() } }) {
                            Icon(Icons.Filled.Menu, contentDescription = "Menú")
                        }
                    },
                    actions = {
                        HostMenu(vm)
                        IconButton(onClick = { vm.refresh() }) {
                            Icon(Icons.Filled.Refresh, contentDescription = "Refrescar")
                        }
                        IconButton(onClick = { showDrawerMenu = true }) {
                            Icon(Icons.Filled.MoreVert, contentDescription = "Más")
                        }
                    },
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = MaterialTheme.colorScheme.surface,
                    ),
                )
            },
            bottomBar = {
                NavigationBar {
                    BottomItem(vm, Section.DASHBOARD)
                    BottomItem(vm, Section.HOSTS)
                    BottomItem(vm, Section.ALERTS)
                    BottomItem(vm, Section.SETTINGS)
                }
            },
        ) { innerPadding ->
            Column(
                Modifier
                    .fillMaxSize()
                    .padding(innerPadding),
            ) {
                if (vm.busy) {
                    LinearProgressIndicator(Modifier.fillMaxWidth())
                }
                if (vm.error != null) {
                    ErrorCard(vm.error ?: "")
                }
                Box(
                    Modifier
                        .fillMaxSize()
                        .weight(1f),
                ) {
                    when (vm.section) {
                        Section.DASHBOARD -> DashboardScreen(vm)
                        Section.HOSTS -> HostsScreen(vm)
                        Section.CONTAINERS -> ContainersScreen(vm)
                        Section.SERVICES -> ServicesScreen(vm)
                        Section.NETWORK -> NetworkScreen(vm)
                        Section.ALERTS -> AlertsScreen(vm)
                        Section.HISTORY -> HistoryScreen(vm)
                        Section.MONITORS -> MonitorsScreen(vm)
                        Section.AUDIT -> AuditScreen(vm)
                        Section.SYSTEM -> SystemScreen(vm)
                        Section.SECURITY -> SecurityScreen(vm)
                        Section.TERMINAL -> TerminalScreen(vm)
                        Section.ASSISTANT -> AssistantScreen(vm)
                        Section.SETTINGS -> SettingsScreen(vm)
                    }
                }
            }
        }
    }

    if (vm.detailTitle != null) {
        AlertDialog(
            onDismissRequest = { vm.clearDetail() },
            title = { Text(vm.detailTitle ?: "") },
            text = {
                SelectionContainer {
                    Text(
                        vm.detailBody ?: "",
                        fontFamily = FontFamily.Monospace,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier
                            .fillMaxWidth()
                            .verticalScroll(rememberScrollState()),
                    )
                }
            },
            confirmButton = {
                TextButton(onClick = { vm.clearDetail() }) { Text("Cerrar") }
            },
        )
    }

    if (showDrawerMenu) {
        AlertDialog(
            onDismissRequest = { showDrawerMenu = false },
            title = { Text("Accesos rápidos") },
            text = {
                Column {
                    Section.entries.forEach { section ->
                        TextButton(
                            onClick = {
                                vm.selectSection(section)
                                showDrawerMenu = false
                            },
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Row(
                                Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.Start,
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Icon(section.icon, contentDescription = null)
                                Text(section.label, modifier = Modifier.padding(start = 12.dp))
                            }
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { showDrawerMenu = false }) { Text("Cerrar") }
            },
        )
    }
}

@Composable
private fun HostMenu(vm: NexusViewModel) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        TextButton(onClick = { expanded = true }) {
            Text(
                vm.selectedHost?.name?.take(20) ?: "Host",
                maxLines = 1,
            )
            Icon(Icons.Filled.ArrowDropDown, contentDescription = null)
        }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            if (vm.hosts.isEmpty()) {
                DropdownMenuItem(
                    text = { Text("Sin hosts") },
                    onClick = { expanded = false },
                )
            }
            vm.hosts.forEach { host ->
                DropdownMenuItem(
                    text = {
                        Text(
                            "${host.name}${if (host.online) " · online" else " · offline"}",
                        )
                    },
                    leadingIcon = {
                        if (host.id == vm.selectedHostId) {
                            Icon(Icons.Filled.Notifications, contentDescription = null)
                        }
                    },
                    onClick = {
                        expanded = false
                        vm.selectHost(host.id)
                    },
                )
            }
        }
    }
}

@Composable
private fun androidx.compose.foundation.layout.RowScope.BottomItem(
    vm: NexusViewModel,
    section: Section,
) {
    NavigationBarItem(
        selected = vm.section == section,
        onClick = { vm.selectSection(section) },
        icon = { Icon(section.icon, contentDescription = null) },
        label = { Text(section.label) },
    )
}
