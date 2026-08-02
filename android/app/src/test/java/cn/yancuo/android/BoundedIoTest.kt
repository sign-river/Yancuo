package cn.yancuo.android

import cn.yancuo.android.data.io.InputSizeLimitException
import cn.yancuo.android.data.io.copyToFileLimited
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
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
}
