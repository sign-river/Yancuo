package cn.yancuo.android

import cn.yancuo.android.data.identity.IdentityException
import cn.yancuo.android.data.identity.IdentityStore
import cn.yancuo.android.data.identity.LocalIdentity
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class IdentityStoreTest {
    @get:Rule
    val tmp: TemporaryFolder = TemporaryFolder()

    @Test
    fun loadOrCreate_upgradesLegacyIdentityWithProfileFields() {
        val file = File(tmp.root, "identity.json")
        file.writeText(
            JSONObject()
                .put("user_id", "usr_legacy")
                .put("device_id", "dev_android_legacy")
                .put("database_id", "db_legacy")
                .toString(),
        )

        val identity = IdentityStore(file).loadOrCreate()

        assertTrue(identity.profileId.startsWith("profile_"))
        assertEquals("", identity.lastSnapshotId)
        val upgraded = JSONObject(file.readText())
        assertEquals(identity.profileId, upgraded.getString("profile_id"))
    }

    @Test
    fun loadOrCreate_rejectsOversizedIdentity() {
        val file = File(tmp.root, "oversized.json").apply {
            writeBytes(ByteArray(64 * 1024 + 1))
        }

        assertThrows(IdentityException::class.java) { IdentityStore(file).loadOrCreate() }
    }

    @Test
    fun prepareImportedForRestore_preservesLocalDeviceAndClearsRemoteHead() {
        val localFile = File(tmp.root, "local.json")
        val localStore = IdentityStore(localFile)
        localStore.save(identity(deviceId = "dev_android_local", databaseId = "db_local"))
        val importedFile = File(tmp.root, "imported.json")
        IdentityStore(importedFile).save(
            identity(
                deviceId = "dev_win_source",
                databaseId = "db_imported",
                profileId = "profile_imported",
                lastSnapshotId = "snapshot_remote",
            ),
        )

        val merged = localStore.prepareImportedForRestore(
            importedFile,
            expectedDatabaseId = "db_imported",
            expectedProfileId = "profile_imported",
        )

        assertEquals("dev_android_local", merged.deviceId)
        assertEquals("db_imported", merged.databaseId)
        assertEquals("profile_imported", merged.profileId)
        assertEquals("", merged.lastSnapshotId)
        assertEquals("dev_android_local", IdentityStore(importedFile).loadOrCreate().deviceId)
    }

    @Test
    fun prepareImportedForRestore_rejectsManifestMismatch() {
        val localStore = IdentityStore(File(tmp.root, "mismatch-local.json"))
        localStore.save(identity(deviceId = "dev_android_local"))
        val importedFile = File(tmp.root, "mismatch-imported.json")
        IdentityStore(importedFile).save(identity(databaseId = "db_wrong"))

        assertThrows(IdentityException::class.java) {
            localStore.prepareImportedForRestore(
                importedFile,
                expectedDatabaseId = "db_expected",
                expectedProfileId = null,
            )
        }
    }

    private fun identity(
        deviceId: String = "dev_win_source",
        databaseId: String = "db_database",
        profileId: String = "profile_profile",
        lastSnapshotId: String = "",
    ) = LocalIdentity(
        userId = "usr_user",
        deviceId = deviceId,
        databaseId = databaseId,
        profileId = profileId,
        lastSnapshotId = lastSnapshotId,
        displayName = "测试资料",
        createdAt = "2026-08-03T00:00:00Z",
    )
}
