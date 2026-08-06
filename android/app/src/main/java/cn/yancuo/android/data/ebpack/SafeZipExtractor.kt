package cn.yancuo.android.data.ebpack

import cn.yancuo.android.data.io.InputSizeLimitException
import cn.yancuo.android.data.io.MAX_EBPACK_BYTES
import cn.yancuo.android.data.io.copyStreamLimited
import java.io.File
import java.io.FileOutputStream
import java.util.zip.ZipEntry
import java.util.zip.ZipFile

internal const val MAX_EBPACK_MEMBERS = 10_000
internal const val MAX_EBPACK_MEMBER_BYTES: Long = 256L * 1024 * 1024
internal const val MAX_EBPACK_COMPRESSION_RATIO = 1_000.0

internal fun extractEbpackSafely(zipFile: File, destination: File) {
    if (zipFile.length() > MAX_EBPACK_BYTES) {
        throw EbpackException("ebpack 文件超过 512 MiB 上限")
    }
    val canonicalDestination = destination.canonicalFile
    val seen = mutableSetOf<String>()

    try {
        ZipFile(zipFile).use { zip ->
            val entries = mutableListOf<Pair<ZipEntry, String>>()
            var declaredTotal = 0L
            val enumeration = zip.entries()
            while (enumeration.hasMoreElements()) {
                val entry = enumeration.nextElement()
                if (entries.size >= MAX_EBPACK_MEMBERS) {
                    throw EbpackException("ebpack 条目数超过 $MAX_EBPACK_MEMBERS 上限")
                }
                val normalized = normalizeEntryName(entry.name)
                if (!seen.add(normalized)) {
                    throw EbpackException("ebpack 包含重复条目：$normalized")
                }
                declaredTotal = validateDeclaredSize(entry, normalized, declaredTotal)
                entries += entry to normalized
            }

            var totalWritten = 0L
            entries.forEach { (entry, normalized) ->
                val outputFile = File(destination, normalized).canonicalFile
                if (
                    outputFile != canonicalDestination &&
                    !outputFile.path.startsWith(canonicalDestination.path + File.separator)
                ) {
                    throw EbpackException("非法 zip 路径：${entry.name}")
                }
                if (entry.isDirectory) {
                    if (outputFile.exists() && !outputFile.isDirectory) {
                        throw EbpackException("ebpack 文件与目录冲突：$normalized")
                    }
                    outputFile.mkdirs()
                } else {
                    if (outputFile.exists()) throw EbpackException("ebpack 条目目标已存在：$normalized")
                    outputFile.parentFile?.mkdirs()
                    val remainingTotal = MAX_EBPACK_BYTES - totalWritten
                    val currentLimit = minOf(MAX_EBPACK_MEMBER_BYTES, remainingTotal)
                    val written = try {
                        zip.getInputStream(entry).use { input ->
                            FileOutputStream(outputFile).use { output ->
                                copyStreamLimited(input, output, currentLimit)
                            }
                        }
                    } catch (_: InputSizeLimitException) {
                        outputFile.delete()
                        throw EbpackException("ebpack 条目或解压总大小超过安全上限：$normalized")
                    } catch (exc: Exception) {
                        outputFile.delete()
                        throw exc
                    }
                    if (written != entry.size) {
                        outputFile.delete()
                        throw EbpackException("ebpack 条目实际大小与声明不一致：$normalized")
                    }
                    totalWritten += written
                }
            }
        }
    } catch (exc: EbpackException) {
        throw exc
    } catch (exc: Exception) {
        throw EbpackException("ebpack 解压失败：${exc.message ?: exc.javaClass.simpleName}")
    }
}

private fun normalizeEntryName(rawName: String): String {
    if (rawName.isBlank() || rawName.indexOf('\u0000') >= 0) {
        throw EbpackException("ebpack 条目名称无效")
    }
    val value = rawName.replace('\\', '/')
    if (value.startsWith('/') || Regex("^[A-Za-z]:($|/)").containsMatchIn(value)) {
        throw EbpackException("ebpack 条目必须使用相对路径：$rawName")
    }
    val parts = value.split('/').filter { it.isNotEmpty() && it != "." }
    if (parts.isEmpty() || parts.any { it == ".." || ':' in it }) {
        throw EbpackException("ebpack 条目路径无效：$rawName")
    }
    return parts.joinToString("/")
}

private fun validateDeclaredSize(entry: ZipEntry, name: String, declaredTotal: Long): Long {
    val size = entry.size
    val compressed = entry.compressedSize
    if (size < 0 || compressed < 0) {
        throw EbpackException("ebpack 条目大小无效：$name")
    }
    if (size > MAX_EBPACK_MEMBER_BYTES) {
        throw EbpackException("ebpack 条目超过 256 MiB 上限：$name")
    }
    if (size > MAX_EBPACK_BYTES - declaredTotal) {
        throw EbpackException("ebpack 解压总大小超过 512 MiB 上限")
    }
    if (
        !entry.isDirectory && size > 0 && compressed >= 0 &&
        (compressed == 0L || size.toDouble() / compressed > MAX_EBPACK_COMPRESSION_RATIO)
    ) {
        throw EbpackException("ebpack 条目压缩比异常：$name")
    }
    return declaredTotal + size
}
