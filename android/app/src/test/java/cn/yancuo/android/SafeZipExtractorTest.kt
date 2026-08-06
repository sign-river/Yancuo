package cn.yancuo.android

import cn.yancuo.android.data.ebpack.EbpackException
import cn.yancuo.android.data.ebpack.extractEbpackSafely
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.io.FileOutputStream
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

class SafeZipExtractorTest {
    @get:Rule
    val tmp: TemporaryFolder = TemporaryFolder()

    @Test
    fun extractEbpackSafely_extractsOrdinaryFile() {
        val zip = createZip(listOf("database/snapshot.sqlite" to "db"))
        val destination = tmp.newFolder("ordinary")

        extractEbpackSafely(zip, destination)

        assertEquals("db", File(destination, "database/snapshot.sqlite").readText())
    }

    @Test
    fun extractEbpackSafely_rejectsTraversal() {
        val zip = createZip(listOf("../outside.txt" to "escaped"))
        val destination = tmp.newFolder("traversal")

        assertThrows(EbpackException::class.java) { extractEbpackSafely(zip, destination) }

        assertFalse(File(destination.parentFile, "outside.txt").exists())
    }

    @Test
    fun extractEbpackSafely_rejectsDuplicateNormalizedPath() {
        val zip = createZip(listOf("assets/item" to "one", "assets/./item" to "two"))
        val destination = tmp.newFolder("duplicate")

        assertThrows(EbpackException::class.java) { extractEbpackSafely(zip, destination) }
    }

    @Test
    fun extractEbpackSafely_rejectsExtremeCompressionRatio() {
        val zip = File(tmp.root, "compression-bomb.ebpack")
        ZipOutputStream(FileOutputStream(zip)).use { output ->
            output.putNextEntry(ZipEntry("database/snapshot.sqlite"))
            output.write(ByteArray(2 * 1024 * 1024))
            output.closeEntry()
        }

        assertThrows(EbpackException::class.java) {
            extractEbpackSafely(zip, tmp.newFolder("compression-bomb"))
        }
    }

    private fun createZip(entries: List<Pair<String, String>>): File {
        val zip = File(tmp.root, "pack-${System.nanoTime()}.ebpack")
        ZipOutputStream(FileOutputStream(zip)).use { output ->
            entries.forEach { (name, content) ->
                output.putNextEntry(ZipEntry(name))
                output.write(content.toByteArray())
                output.closeEntry()
            }
        }
        return zip
    }
}
