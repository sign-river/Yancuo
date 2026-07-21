package cn.yancuo.android

import cn.yancuo.android.data.assets.ObjectStore
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class ObjectStoreTest {

    @get:Rule
    val tmp: TemporaryFolder = TemporaryFolder()

    /** protocol/test-vectors/hash-v1 */
    private val vectorBytes: ByteArray =
        byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte()) +
            "yancuo-hash-vector".toByteArray(Charsets.US_ASCII)

    private val expectedSha =
        "bb35a354143fe5e6514b4c23ec0ac62f1f6c82d515c5d3989aa5b33eb3ea2bc6"

    @Test
    fun hashVector_matchesSharedSpec() {
        val store = ObjectStore(tmp.newFolder("objects"))
        assertEquals(expectedSha, store.hashBytes(vectorBytes))
    }

    @Test
    fun objectPath_usesTwoCharPrefix() {
        val objects = tmp.newFolder("objects")
        val store = ObjectStore(objects)
        val src = File(tmp.root, "vector.bin")
        src.writeBytes(vectorBytes)
        val stored = store.storeCopy(src, role = "original")
        assertEquals(expectedSha, stored.sha256)
        assertEquals("objects/bb/$expectedSha.bin", stored.relativePath)
        assertTrue(stored.absolutePath.isFile)
        assertEquals(
            File(objects, "bb/$expectedSha.bin").canonicalFile,
            stored.absolutePath.canonicalFile,
        )

        val again = store.storeCopy(src, role = "original")
        assertTrue(again.alreadyExisted)
        assertEquals(stored.sha256, again.sha256)
    }
}
