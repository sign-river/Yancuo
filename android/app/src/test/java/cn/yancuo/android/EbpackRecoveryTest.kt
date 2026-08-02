package cn.yancuo.android

import cn.yancuo.android.data.ebpack.createEbpackRestorePlan
import cn.yancuo.android.data.ebpack.recoverInterruptedEbpack
import cn.yancuo.android.data.paths.DataPaths
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class EbpackRecoveryTest {
    @get:Rule
    val tmp: TemporaryFolder = TemporaryFolder()

    @Test
    fun recoverInterruptedEbpack_rollsBackPartiallyInstalledPayload() {
        val paths = paths("partial")
        paths.database.writeText("old-db")
        File(paths.assetDir, "old.txt").writeText("old-asset")
        paths.identityFile.writeText("old-identity")
        val staging = File(paths.root, ".ebpack_final_staging").also { it.mkdirs() }
        File(staging, "error_book.db").writeText("new-db")
        File(staging, "assets").mkdirs()
        File(staging, "identity.json").writeText("new-identity")
        val previous = File(paths.root, ".ebpack_previous")
        createEbpackRestorePlan(
            previous,
            listOf(paths.database, paths.assetDir, paths.identityFile),
        )
        paths.database.renameTo(File(previous, "error_book.db"))
        paths.assetDir.renameTo(File(previous, "assets"))
        File(staging, "error_book.db").renameTo(paths.database)

        assertTrue(recoverInterruptedEbpack(paths))

        assertEquals("old-db", paths.database.readText())
        assertEquals("old-asset", File(paths.assetDir, "old.txt").readText())
        assertEquals("old-identity", paths.identityFile.readText())
        assertFalse(previous.exists())
        assertFalse(staging.exists())
    }

    @Test
    fun recoverInterruptedEbpack_removesInstalledTargetThatHadNoPreviousValue() {
        val paths = paths("new-target")
        paths.database.writeText("old-db")
        File(paths.assetDir, "old.txt").writeText("old-asset")
        val staging = File(paths.root, ".ebpack_final_staging").also { it.mkdirs() }
        File(staging, "error_book.db").writeText("new-db")
        File(staging, "assets").mkdirs()
        File(staging, "identity.json").writeText("new-identity")
        val previous = File(paths.root, ".ebpack_previous")
        createEbpackRestorePlan(
            previous,
            listOf(paths.database, paths.assetDir, paths.identityFile),
        )
        paths.database.renameTo(File(previous, "error_book.db"))
        paths.assetDir.renameTo(File(previous, "assets"))
        File(staging, "identity.json").renameTo(paths.identityFile)

        recoverInterruptedEbpack(paths)

        assertFalse(paths.identityFile.exists())
        assertEquals("old-db", paths.database.readText())
        assertEquals("old-asset", File(paths.assetDir, "old.txt").readText())
    }

    @Test
    fun recoverInterruptedEbpack_discardsStagingWhenInstallationNeverStarted() {
        val paths = paths("staging-only")
        val staging = File(paths.root, ".ebpack_final_staging").also { it.mkdirs() }
        File(staging, "error_book.db").writeText("new-db")

        assertFalse(recoverInterruptedEbpack(paths))

        assertFalse(staging.exists())
    }

    @Test
    fun recoverInterruptedEbpack_restoresLegacyBackupWithoutPlan() {
        val paths = paths("legacy")
        paths.database.writeText("new-db")
        val previous = File(paths.root, ".ebpack_previous").also { it.mkdirs() }
        File(previous, "error_book.db").writeText("old-db")
        File(paths.root, ".ebpack_final_staging").mkdirs()

        assertTrue(recoverInterruptedEbpack(paths))

        assertEquals("old-db", paths.database.readText())
        assertFalse(previous.exists())
    }

    private fun paths(name: String): DataPaths {
        val root = tmp.newFolder(name)
        val assets = File(root, "assets").also { it.mkdirs() }
        return DataPaths(
            root = root,
            database = File(root, "error_book.db"),
            assetDir = assets,
            assetObjectsDir = File(assets, "objects"),
            identityFile = File(root, "identity.json"),
            cacheDir = File(root, "cache").also { it.mkdirs() },
        )
    }
}
