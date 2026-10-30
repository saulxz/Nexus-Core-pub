package com.nexuscore.mobile.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val NexusColors = darkColorScheme(
    primary = Color(0xFF2F81F7),
    onPrimary = Color.White,
    secondary = Color(0xFF9D8CFF),
    onSecondary = Color(0xFF0D1117),
    tertiary = Color(0xFF3FB950),
    background = Color(0xFF0D1117),
    onBackground = Color(0xFFE6EDF3),
    surface = Color(0xFF161B22),
    onSurface = Color(0xFFE6EDF3),
    surfaceVariant = Color(0xFF1C2128),
    onSurfaceVariant = Color(0xFFB3BCC8),
    outline = Color(0xFF444C56),
    error = Color(0xFFFF6B6B),
)

@Composable
fun NexusTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = NexusColors,
        typography = Typography(),
        content = content,
    )
}
