package cn.yancuo.android.data.ebpack

import cn.yancuo.android.data.assets.ObjectStore
import cn.yancuo.android.data.db.YancuoDb
import cn.yancuo.android.data.identity.IdentityStore
import cn.yancuo.android.data.paths.DataPaths
import cn.yancuo.android.domain.EBPACK_FORMAT
import cn.yancuo.android.domain.EBPACK_FORMAT_VERSION
import cn.yancuo.android.domain.SCHEMA_VERSION
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.util.zip.ZipInputStream

class EbpackException(message: String) : Exception(message)

data class EbpackImportResult(
    val schemaVersion: Int,
    val problemCount: Int,
    val note: String,
)

/**
 * 导入 `.ebpack` v1：解压、校验 manifest 与 checksums，全量替换数据根。
 * v1 策略：完整替换 DB + assets；identity 默认全量替换（见 [IdentityStore.mergeFromImported] 注释）。
 */
class EbpackImporter(
    private val paths: DataPaths,
    private val identityStore: IdentityStore,
) {

    fun importPack(packFile: File): EbpackImportResult {
        if (!packFile.isFile) throw EbpackException("ebpack 文件不存在")
        val tmp = File(paths.cacheDir, "ebpack-import-${System.currentTimeMillis()}")
        val staging = File(paths.root, ".ebpack_final_staging")
        try {
            if (tmp.exists()) tmp.deleteRecursively()
            tmp.mkdirs()
            unzip(packFile, tmp)
            val manifest = validateAndChecksum(tmp)
            val dbSrc = File(tmp, "database/snapshot.sqlite")
            if (!dbSrc.isFile) throw EbpackException("缺少 database/snapshot.sqlite")

            YancuoDb.resetInstance()

            if (staging.exists()) staging.deleteRecursively()
            staging.mkdirs()
            dbSrc.copyTo(File(staging, "error_book.db"), overwrite = true)
            val assetsSrc = File(tmp, "assets")
            if (assetsSrc.isDirectory) {
                assetsSrc.copyRecursively(File(staging, "assets"), overwrite = true)
            } else {
                File(staging, "assets/objects").mkdirs()
            }
            val identitySrc = File(tmp, "identity.json")
            if (identitySrc.isFile) {
                identitySrc.copyTo(File(staging, "identity.json"), overwrite = true)
            }

            // 替换正式位置
            if (paths.database.exists()) paths.database.delete()
            if (paths.assetDir.exists()) paths.assetDir.deleteRecursively()
            File(staging, "error_book.db").copyTo(paths.database, overwrite = true)
            File(staging, "assets").copyRecursively(paths.assetDir, overwrite = true)
            val idStaged = File(staging, "identity.json")
            if (idStaged.isFile) {
                // v1 全量替换；若需保留本机 device_id 可改为 keepLocalDeviceId=true
                identityStore.mergeFromImported(idStaged, keepLocalDeviceId = false)
            }
            paths.ensureDirectories()

            val schema = manifest.optInt("schema_version", 0)
            return EbpackImportResult(
                schemaVersion = schema,
                problemCount = manifest.optInt("problem_count", 0),
                note = "已全量替换本地库与资源（含 identity.json，若包内存在）",
            )
        } finally {
            tmp.deleteRecursively()
            staging.deleteRecursively()
        }
    }

    private fun validateAndChecksum(root: File): JSONObject {
        val required = listOf(
            "manifest.json",
            "checksums.sha256",
            "database/snapshot.sqlite",
            "database/migrations.json",
            "assets/index.json",
        )
        for (rel in required) {
            if (!File(root, rel).isFile) throw EbpackException("ebpack 缺少条目：$rel")
        }
        val manifest = JSONObject(File(root, "manifest.json").readText(Charsets.UTF_8))
        if (manifest.optString("format") != EBPACK_FORMAT) {
            throw EbpackException("不是研错库 ebpack（format 不匹配）")
        }
        if (manifest.optInt("format_version", 0) != EBPACK_FORMAT_VERSION) {
            throw EbpackException("ebpack format_version 不受支持")
        }
        if (manifest.optBoolean("encrypted", false)) {
            throw EbpackException("v1 尚未实现加密包解密，拒绝导入")
        }
        val pkgSchema = manifest.optInt("schema_version", 0)
        if (pkgSchema > SCHEMA_VERSION) {
            throw EbpackException(
                "包 schema_version=$pkgSchema 高于程序支持的 $SCHEMA_VERSION，请升级软件",
            )
        }
        verifyChecksums(root)
        return manifest
    }

    private fun verifyChecksums(root: File) {
        val table = File(root, "checksums.sha256")
        val hasher = ObjectStore(File(root, "assets/objects"))
        for (line in table.readLines(Charsets.UTF_8)) {
            val trimmed = line.trim()
            if (trimmed.isEmpty() || trimmed.startsWith("#")) continue
            val parts = trimmed.split("  ", limit = 2)
            if (parts.size != 2) throw EbpackException("checksums 行格式错误：${trimmed.take(80)}")
            val expected = parts[0].trim()
            val rel = parts[1].trim()
            val path = File(root, rel)
            if (!path.isFile) throw EbpackException("checksums 引用缺失：$rel")
            val actual = hasher.hashFile(path)
            if (actual != expected) throw EbpackException("校验失败：$rel")
        }
    }

    private fun unzip(zipFile: File, destDir: File) {
        ZipInputStream(BufferedInputStream(FileInputStream(zipFile))).use { zis ->
            var entry = zis.nextEntry
            while (entry != null) {
                val outFile = File(destDir, entry.name)
                val canonicalDest = destDir.canonicalFile
                val canonicalOut = outFile.canonicalFile
                if (!canonicalOut.path.startsWith(canonicalDest.path + File.separator) &&
                    canonicalOut != canonicalDest
                ) {
                    throw EbpackException("非法 zip 路径：${entry.name}")
                }
                if (entry.isDirectory) {
                    outFile.mkdirs()
                } else {
                    outFile.parentFile?.mkdirs()
                    FileOutputStream(outFile).use { fos ->
                        zis.copyTo(fos)
                    }
                }
                zis.closeEntry()
                entry = zis.nextEntry
            }
        }
    }
}
