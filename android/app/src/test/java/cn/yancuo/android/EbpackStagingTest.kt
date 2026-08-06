package cn.yancuo.android

import cn.yancuo.android.data.ebpack.EbpackException
import cn.yancuo.android.data.ebpack.stageExtractedEbpack
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class EbpackStagingTest {
    @get:Rule
    val tmp: TemporaryFolder = TemporaryFolder()

    @Test
    fun stageExtractedEbpack_movesPayloadWithoutDuplicatingIt() {
        val extracted = tmp.newFolder("extracted")
        val database = File(extracted, "database/snapshot.sqlite").also {
            it.parentFile?.mkdirs()
            it.writeText("database")
        }
        val asset = File(extracted, "assets/objects/aa/item.png").also {
            it.parentFile?.mkdirs()
            it.writeText("asset")
        }
        File(extracted, "identity.json").writeText("identity")
        val staging = File(tmp.root, "staging")

        stageExtractedEbpack(extracted, staging)

        assertEquals("database", File(staging, "error_book.db").readText())
        assertEquals("asset", File(staging, "assets/objects/aa/item.png").readText())
        assertEquals("identity", File(staging, "identity.json").readText())
        assertFalse(database.exists())
        assertFalse(asset.exists())
    }

    @Test
    fun stageExtractedEbpack_preflightsCollisionBeforeMovingSources() {
        val extracted = tmp.newFolder("collision-source")
        val database = File(extracted, "database/snapshot.sqlite").also {
            it.parentFile?.mkdirs()
            it.writeText("database")
        }
        File(extracted, "assets").mkdirs()
        val staging = tmp.newFolder("collision-staging")
        File(staging, "assets").mkdirs()

        assertThrows(EbpackException::class.java) {
            stageExtractedEbpack(extracted, staging)
        }

        assertTrue(database.isFile)
        assertFalse(File(staging, "error_book.db").exists())
    }
}
