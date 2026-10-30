package com.nexuscore.mobile.ui

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Build
import androidx.compose.material.icons.filled.Computer
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.FactCheck
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.List
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Memory
import androidx.compose.material.icons.filled.MonitorHeart
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.ShowChart
import androidx.compose.material.icons.filled.Storage
import androidx.compose.material.icons.filled.Wifi
import androidx.compose.ui.graphics.vector.ImageVector

enum class Section(val label: String, val icon: ImageVector) {
    DASHBOARD("Resumen", Icons.Filled.Dashboard),
    HOSTS("Hosts", Icons.Filled.List),
    CONTAINERS("Contenedores", Icons.Filled.Storage),
    SERVICES("Servicios", Icons.Filled.Build),
    NETWORK("Red", Icons.Filled.Wifi),
    ALERTS("Alertas", Icons.Filled.Notifications),
    HISTORY("Histórico", Icons.Filled.ShowChart),
    MONITORS("Monitores", Icons.Filled.MonitorHeart),
    AUDIT("Auditoría", Icons.Filled.FactCheck),
    SYSTEM("Sistema", Icons.Filled.Memory),
    SECURITY("Seguridad", Icons.Filled.Lock),
    TERMINAL("Terminal", Icons.Filled.Computer),
    ASSISTANT("Asistente", Icons.Filled.Search),
    SETTINGS("Ajustes", Icons.Filled.Settings),
}
