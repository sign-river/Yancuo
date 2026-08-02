package cn.yancuo.android

import cn.yancuo.android.data.io.InputSizeLimitException
import cn.yancuo.android.data.io.copyToFileLimited
import cn.yancuo.android.data.io.readFileLimited
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.Rule
import java.io.ByteArrayInputStream
import java.io.File

class BoundedIoTest {
    @get:Rule
    val tmp: TemporaryFolder = TemporaryFolder()

    @Test
    fun copyToFileLimited_writesInputWithinBudget() {
        val payload = "bounded import".toByteArray()
        val destination = File(tmp.root, "result.bin")

        copyToFileLimited(ByteArrayInputStream(payload), destination, payload.size.toLong())

        assertArrayEquals(payload, destination.readBytes())
    }

    @Test
    fun copyToFileLimited_removesPartialFileWhenBudgetExceeded() {
        val destination = File(tmp.root, "partial.bin")

        assertThrows(InputSizeLimitException::class.java) {
            copyToFileLimited(ByteArrayInputStream(ByteArray(32)), destination, 16)
        }

        assertFalse(destination.exists())
    }

    @Test
    fun readFileLimited_rejectsFileBeforeAllocatingBeyondBudget() {
        val source = File(tmp.root, "metadata.json").apply { writeBytes(ByteArray(17)) }

        assertThrows(InputSizeLimitException::class.java) { readFileLimited(source, 16) }

        assertTrue(source.exists())
    }
}
