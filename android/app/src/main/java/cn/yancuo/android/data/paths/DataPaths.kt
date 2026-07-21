package cn.yancuo.android.data.paths

import android.content.Context
import java.io.File

/**
 * 本地数据根：`filesDir/yancuo_data`。
 * 布局与 Windows 对齐：error_book.db、assets/objects、identity.json、cache。
 */
data class DataPaths(
    val root: File,
    val database: File,
    val assetDir: File,
    val assetObjectsDir: File,
    val identityFile: File,
    val cacheDir: File,
) {
    fun ensureDirectories() {
        listOf(root, assetDir, assetObjectsDir, cacheDir).forEach { it.mkdirs() }
    }

    companion object {
        fun from(context: Context): DataPaths {
            val root = File(context.filesDir, "yancuo_data")
            val assetDir = File(root, "assets")
            return DataPaths(
                root = root,
                database = File(root, "error_book.db"),
                assetDir = assetDir,
                assetObjectsDir = File(assetDir, "objects"),
                identityFile = File(root, "identity.json"),
                cacheDir = File(root, "cache"),
            ).also { it.ensureDirectories() }
        }
    }
}
