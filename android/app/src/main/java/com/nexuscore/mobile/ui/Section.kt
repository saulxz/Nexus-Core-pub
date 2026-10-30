package com.nexuscore.mobile.ui

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Build
import androidx.compose.material.icons.outlined.Computer
import androidx.compose.material.icons.outlined.Dashboard
import androidx.compose.material.icons.outlined.FactCheck
import androidx.compose.material.icons.outlined.History
import androidx.compose.material.icons.outlined.List
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.Memory
import androidx.compose.material.icons.outlined.MonitorHeart
import androidx.compose.material.icons.outlined.Notifications
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.ShowChart
import androidx.compose.material.icons.outlined.Storage
import androidx.compose.material.icons.outlined.Wifi
import androidx.compose.ui.graphics.vector.ImageVector

enum class Section(val label: String, val icon: ImageVector) {
    DASHBOARD("Resumen", Icons.Outlined.Dashboard),
    HOSTS("Hosts", Icons.Outlined.List),
    CONTAINERS("Contenedores", Icons.Outlined.Storage),
    SERVICES("Servicios", Icons.Outlined.Build),
    NETWORK("Red", Icons.Outlined.Wifi),
    ALERTS("Alertas", Icons.Outlined.Notifications),
    HISTORY("Histórico", Icons.Outlined.ShowChart),
    MONITORS("Monitores", Icons.Outlined.MonitorHeart),
    AUDIT("Auditoría", Icons.Outlined.FactCheck),
    SYSTEM("Sistema", Icons.Outlined.Memory),
    SECURITY("Seguridad", Icons.Outlined.Lock),
    TERMINAL("Terminal", Icons.Outlined.Computer),
    ASSISTANT("Asistente", Icons.Outlined.Search),
    SETTINGS("Ajustes", Icons.Outlined.Settings),
}

