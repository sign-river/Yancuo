package cn.yancuo.android.data.identity

import cn.yancuo.android.domain.newDeviceId
import cn.yancuo.android.domain.newId
import org.json.JSONObject
import java.io.File
import java.time.Instant

data class LocalIdentity(
    val userId: String,
    val deviceId: String,
    val databaseId: String,
    val displayName: String,
    val createdAt: String,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("user_id", userId)
        .put("device_id", deviceId)
        .put("database_id", databaseId)
        .put("display_name", displayName)
        .put("created_at", createdAt)
}

class IdentityStore(private val identityFile: File) {

    fun loadOrCreate(displayName: String = "安卓用户"): LocalIdentity {
        if (identityFile.isFile) {
            val raw = JSONObject(identityFile.readText(Charsets.UTF_8))
            return LocalIdentity(
                userId = raw.getString("user_id"),
                deviceId = raw.getString("device_id"),
                databaseId = raw.getString("database_id"),
                displayName = raw.optString("display_name", displayName),
                createdAt = raw.optString("created_at", ""),
            )
        }
        val identity = LocalIdentity(
            userId = newId("usr"),
            deviceId = newDeviceId(),
            databaseId = newId("db"),
            displayName = displayName,
            createdAt = Instant.now().toString(),
        )
        save(identity)
        return identity
    }

    fun save(identity: LocalIdentity) {
        identityFile.parentFile?.mkdirs()
        identityFile.writeText(identity.toJson().toString(2) + "\n", Charsets.UTF_8)
    }

    /**
     * 导入 ebpack 时可选替换 identity。
     * v1 全量替换可用；若需保留本机 device_id，传入 keepLocalDeviceId=true。
     */
    fun mergeFromImported(imported: File, keepLocalDeviceId: Boolean = false) {
        if (!imported.isFile) return
        if (!keepLocalDeviceId) {
            imported.copyTo(identityFile, overwrite = true)
            return
        }
        val local = loadOrCreate()
        val raw = JSONObject(imported.readText(Charsets.UTF_8))
        val merged = LocalIdentity(
            userId = raw.optString("user_id", local.userId),
            deviceId = local.deviceId,
            databaseId = raw.optString("database_id", local.databaseId),
            displayName = raw.optString("display_name", local.displayName),
            createdAt = raw.optString("created_at", local.createdAt),
        )
        save(merged)
    }
}
