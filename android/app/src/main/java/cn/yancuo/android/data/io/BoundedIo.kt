package cn.yancuo.android.data.io

import java.io.File
import java.io.FileOutputStream
import java.io.InputStream
import java.io.OutputStream

const val MAX_IMPORT_IMAGE_BYTES: Long = 32L * 1024 * 1024
const val MAX_EBPACK_BYTES: Long = 512L * 1024 * 1024

class InputSizeLimitException(
    val maxBytes: Long,
) : Exception("输入数据超过 ${maxBytes / (1024 * 1024)} MiB 上限")

fun copyStreamLimited(input: InputStream, output: OutputStream, maxBytes: Long): Long {
    require(maxBytes >= 0) { "maxBytes 不能为负数" }
    val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
    var copied = 0L
    while (true) {
        val read = input.read(buffer)
        if (read < 0) return copied
        if (read == 0) continue
        if (copied > maxBytes - read) throw InputSizeLimitException(maxBytes)
        output.write(buffer, 0, read)
        copied += read
    }
}

fun copyToFileLimited(input: InputStream, destination: File, maxBytes: Long): Long {
    destination.parentFile?.mkdirs()
    try {
        FileOutputStream(destination).use { output ->
            return copyStreamLimited(input, output, maxBytes)
        }
    } catch (exc: Exception) {
        destination.delete()
        throw exc
    }
}
