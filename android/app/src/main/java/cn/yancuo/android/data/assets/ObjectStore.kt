package cn.yancuo.android.data.assets

import java.io.File
import java.io.FileInputStream
import java.io.InputStream
import java.security.MessageDigest

data class StoredObject(
    val sha256: String,
    val relativePath: String,
    val absolutePath: File,
    val sizeBytes: Long,
    val mimeType: String?,
    val alreadyExisted: Boolean,
)

/**
 * SHA-256 内容寻址对象库。
 * 路径：`objects/{sha256[0:2]}/{sha256}{ext}`；已存在哈希绝不覆盖。
 */
class ObjectStore(private val objectsRoot: File) {

    init {
        objectsRoot.mkdirs()
    }

    fun hashFile(path: File): String = FileInputStream(path).use { hashStream(it) }

    fun hashBytes(data: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256")
        digest.update(data)
        return digest.digest().toHex()
    }

    fun hashStream(stream: InputStream): String {
        val digest = MessageDigest.getInstance("SHA-256")
        val buf = ByteArray(1024 * 1024)
        while (true) {
            val n = stream.read(buf)
            if (n <= 0) break
            digest.update(buf, 0, n)
        }
        return digest.digest().toHex()
    }

    fun objectPath(sha256: String, suffix: String): File =
        File(File(objectsRoot, sha256.take(2)), "$sha256$suffix")

    fun relativeOf(sha256: String, suffix: String): String =
        "objects/${sha256.take(2)}/$sha256$suffix"

    /**
     * 复制源文件入对象库。role=original 时尽力设为只读。
     * 若目标已存在（同哈希），不覆盖写入。
     */
    fun storeCopy(source: File, role: String = "original"): StoredObject {
        require(source.isFile) { "文件不存在：$source" }
        val sha = hashFile(source)
        val suffix = source.extension.let { if (it.isBlank()) ".bin" else ".${it.lowercase()}" }
        val dest = objectPath(sha, suffix)
        val rel = relativeOf(sha, suffix)
        val already = dest.isFile
        if (!already) {
            dest.parentFile?.mkdirs()
            source.copyTo(dest, overwrite = false)
            if (role == "original") {
                try {
                    dest.setWritable(false)
                } catch (_: Exception) {
                    // 尽力而为
                }
            }
        }
        val mime = guessMime(source.name)
        return StoredObject(
            sha256 = sha,
            relativePath = rel,
            absolutePath = dest,
            sizeBytes = dest.length(),
            mimeType = mime,
            alreadyExisted = already,
        )
    }

    fun resolve(relativePath: String): File {
        val rel = relativePath.replace('\\', '/')
        return if (rel.startsWith("objects/")) {
            File(objectsRoot.parentFile, rel)
        } else {
            File(objectsRoot, rel)
        }
    }

    companion object {
        fun guessMime(name: String): String? {
            val lower = name.lowercase()
            return when {
                lower.endsWith(".jpg") || lower.endsWith(".jpeg") -> "image/jpeg"
                lower.endsWith(".png") -> "image/png"
                lower.endsWith(".webp") -> "image/webp"
                lower.endsWith(".gif") -> "image/gif"
                else -> null
            }
        }
    }
}

private fun ByteArray.toHex(): String = joinToString("") { b -> "%02x".format(b) }
