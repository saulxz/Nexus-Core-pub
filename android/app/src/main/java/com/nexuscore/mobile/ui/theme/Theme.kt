package com.nexuscore.mobile.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.nexuscore.mobile.R

/** Colores de estado compartidos; el color nunca decora, solo informa. */
object NexusColors {
    val Ok = Color(0xFF2B8A57)
    val Warn = Color(0xFFB07616)
    val Crit = Color(0xFFC2372B)
    val Series = Color(0xFF0E7C6B)

    val DarkOk = Color(0xFF4CC07F)
    val DarkWarn = Color(0xFFE3A33D)
    val DarkCrit = Color(0xFFF0685B)
    val DarkSeries = Color(0xFF3CC3AB)
}

private val Geist = FontFamily(
    Font(R.font.geist_regular, FontWeight.Normal),
    Font(R.font.geist_medium, FontWeight.Medium),
    Font(R.font.geist_semibold, FontWeight.SemiBold),
)

private val GeistMono = FontFamily(
    Font(R.font.geist_mono_regular, FontWeight.Normal),
    Font(R.font.geist_mono_medium, FontWeight.Medium),
)

private val NexusTypography = Typography(
    displaySmall = TextStyle(
        fontFamily = Geist,
        fontWeight = FontWeight.SemiBold,
        fontSize = 32.sp,
        lineHeight = 38.sp,
        letterSpacing = (-0.04).sp,
    ),
    headlineSmall = TextStyle(
        fontFamily = Geist,
        fontWeight = FontWeight.SemiBold,
        fontSize = 24.sp,
        lineHeight = 30.sp,
        letterSpacing = (-0.035).sp,
    ),
    titleLarge = TextStyle(
        fontFamily = Geist,
        fontWeight = FontWeight.SemiBold,
        fontSize = 20.sp,
        lineHeight = 26.sp,
        letterSpacing = (-0.03).sp,
    ),
    titleMedium = TextStyle(
        fontFamily = Geist,
        fontWeight = FontWeight.Medium,
        fontSize = 15.sp,
        lineHeight = 22.sp,
        letterSpacing = (-0.02).sp,
    ),
    bodyLarge = TextStyle(
        fontFamily = Geist,
        fontWeight = FontWeight.Normal,
        fontSize = 14.sp,
        lineHeight = 21.sp,
        letterSpacing = (-0.01).sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = Geist,
        fontWeight = FontWeight.Normal,
        fontSize = 13.sp,
        lineHeight = 20.sp,
        letterSpacing = (-0.01).sp,
    ),
    bodySmall = TextStyle(
        fontFamily = Geist,
        fontWeight = FontWeight.Normal,
        fontSize = 12.sp,
        lineHeight = 18.sp,
    ),
    labelLarge = TextStyle(
        fontFamily = Geist,
        fontWeight = FontWeight.Medium,
        fontSize = 12.sp,
        lineHeight = 16.sp,
    ),
    labelMedium = TextStyle(
        fontFamily = Geist,
        fontWeight = FontWeight.Medium,
        fontSize = 11.sp,
        lineHeight = 15.sp,
        letterSpacing = 0.02.sp,
    ),
    labelSmall = TextStyle(
        fontFamily = Geist,
        fontWeight = FontWeight.Normal,
        fontSize = 10.sp,
        lineHeight = 14.sp,
        letterSpacing = 0.04.sp,
    ),
)

private val NexusShapes = Shapes(
    extraSmall = RoundedCornerShape(6.dp),
    small = RoundedCornerShape(6.dp),
    medium = RoundedCornerShape(8.dp),
    large = RoundedCornerShape(12.dp),
    extraLarge = RoundedCornerShape(12.dp),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFFECEBE8),
    onPrimary = Color(0xFF101113),
    primaryContainer = Color(0xFF1E2024),
    onPrimaryContainer = Color(0xFFECEBE8),
    secondary = Color(0xFF3CC3AB),
    onSecondary = Color(0xFF101113),
    tertiary = Color(0xFF4CC07F),
    onTertiary = Color(0xFF101113),
    background = Color(0xFF101113),
    onBackground = Color(0xFFECEBE8),
    surface = Color(0xFF17181B),
    onSurface = Color(0xFFECEBE8),
    surfaceVariant = Color(0xFF1E2024),
    onSurfaceVariant = Color(0xFF9C9A94),
    outline = Color(0xFF26282D),
    outlineVariant = Color(0xFF202126),
    error = Color(0xFFF0685B),
    onError = Color(0xFF101113),
    errorContainer = Color(0xFF2A1715),
    onErrorContainer = Color(0xFFF0685B),
)

private val LightColors = lightColorScheme(
    primary = Color(0xFF1B1A17),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFECEAE4),
    onPrimaryContainer = Color(0xFF1B1A17),
    secondary = Color(0xFF0E7C6B),
    onSecondary = Color.White,
    tertiary = Color(0xFF2B8A57),
    onTertiary = Color.White,
    background = Color(0xFFF5F4F0),
    onBackground = Color(0xFF1B1A17),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF1B1A17),
    surfaceVariant = Color(0xFFECEAE4),
    onSurfaceVariant = Color(0xFF66645D),
    outline = Color(0xFFE3E1DA),
    outlineVariant = Color(0xFFECEAE4),
    error = Color(0xFFC2372B),
    onError = Color.White,
    errorContainer = Color(0xFFFBEDEA),
    onErrorContainer = Color(0xFFC2372B),
)

@Composable
fun NexusTheme(
    darkTheme: Boolean = true,
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = if (darkTheme) DarkColors else LightColors,
        typography = NexusTypography,
        shapes = NexusShapes,
        content = content,
    )
}

/** Tipografía monoespaciada para cifras y comandos. */
val NexusMono: FontFamily = GeistMono
