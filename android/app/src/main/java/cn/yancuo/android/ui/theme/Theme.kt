package cn.yancuo.android.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val AcademicBlue = Color(0xFF3A5A7A)
private val AcademicBlueDark = Color(0xFF2C455C)
private val SoftSurface = Color(0xFFF2F4F7)
private val SoftSurfaceVariant = Color(0xFFE4E9EF)
private val Ink = Color(0xFF1C2430)
private val Muted = Color(0xFF5A6675)
private val Accent = Color(0xFF4A7C6F)

private val LightColors = lightColorScheme(
    primary = AcademicBlue,
    onPrimary = Color.White,
    primaryContainer = SoftSurfaceVariant,
    onPrimaryContainer = AcademicBlueDark,
    secondary = Accent,
    onSecondary = Color.White,
    background = SoftSurface,
    onBackground = Ink,
    surface = Color.White,
    onSurface = Ink,
    surfaceVariant = SoftSurfaceVariant,
    onSurfaceVariant = Muted,
    outline = Color(0xFFB0BAC6),
)

@Composable
fun YancuoTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = LightColors,
        content = content,
    )
}
