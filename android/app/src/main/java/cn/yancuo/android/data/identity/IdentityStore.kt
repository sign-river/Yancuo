package cn.yancuo.android.data.identity

import cn.yancuo.android.data.io.InputSizeLimitException
import cn.yancuo.android.data.io.readFileLimited
import cn.yancuo.android.domain.newDeviceId
import cn.yancuo.android.domain.newId
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.time.Instant

private const val MAX_IDENTITY_BYTES: Long = 64L * 1024
private val SAFE_ID = Regex("^[A-Za-z0-9_.-]{1,128}$")

class IdentityException(message: String) : Exception(message)

data class LocalIdentity(
    val userId: String,
    val deviceId: String,
    val databaseId: String,
    val profileId: String,
    val lastSnapshotId: String,
    val displayName: String,
    val createdAt: String,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("user_id", userId)
        .put("device_id", deviceId)
        .put("database_id", databaseId)
        .put("profile_id", profileId)
        .put("last_snapshot_id", lastSnapshotId)
        .put("display_name", displayName)
        .put("created_at", createdAt)
}

class IdentityStore(private val identityFile: File) {

    fun loadOrCreate(displayName: String = "安卓用户"): LocalIdentity {
        if (identityFile.exists()) {
            val raw = readRaw(identityFile)
            val identity = validate(raw, displayName)
            if (!raw.has("profile_id") || !raw.has("last_snapshot_id")) save(identity)
            return identity
        }
        val identity = LocalIdentity(
            userId = newId("usr"),
            deviceId = newDeviceId(),
            databaseId = newId("db"),
            profileId = newId("profile"),
            lastSnapshotId = "",
            displayName = displayName,
            createdAt = Instant.now().toString(),
        )
        save(identity)
        return identity
    }

    fun save(identity: LocalIdentity) {
        validate(identity.toJson(), identity.displayName)
        if (Files.isSymbolicLink(identityFile.toPath())) {
            throw IdentityException("identity.json 不能是符号链接")
        }
        identityFile.parentFile?.mkdirs()
        val payload = (identity.toJson().toString(2) + "\n").toByteArray(Charsets.UTF_8)
        if (payload.size > MAX_IDENTITY_BYTES) throw IdentityException("identity.json 过大")
        val temporary = File.createTempFile(".identity-", ".tmp", identityFile.parentFile)
        try {
            FileOutputStream(temporary).use { output ->
                output.write(payload)
                output.flush()
                output.fd.sync()
            }
            if (Files.isSymbolicLink(identityFile.toPath())) {
                throw IdentityException("identity.json 不能是符号链接")
            }
            try {
                Files.move(
                    temporary.toPath(),
                    identityFile.toPath(),
                    StandardCopyOption.ATOMIC_MOVE,
                    StandardCopyOption.REPLACE_EXISTING,
                )
            } catch (_: AtomicMoveNotSupportedException) {
                Files.move(
                    temporary.toPath(),
                    identityFile.toPath(),
                    StandardCopyOption.REPLACE_EXISTING,
                )
            }
        } finally {
            temporary.delete()
        }
    }

    fun prepareImportedForRestore(
        importedFile: File,
        expectedDatabaseId: String?,
        expectedProfileId: String?,
    ): LocalIdentity {
        val imported = validate(readRaw(importedFile), "导入资料")
        if (!expectedDatabaseId.isNullOrBlank() && imported.databaseId != expectedDatabaseId) {
            throw IdentityException("identity.json 与 manifest database_id 不一致")
        }
        if (!expectedProfileId.isNullOrBlank() && imported.profileId != expectedProfileId) {
            throw IdentityException("identity.json 与 manifest profile_id 不一致")
        }
        val local = loadOrCreate()
        val merged = imported.copy(
            deviceId = local.deviceId,
            lastSnapshotId = "",
        )
        IdentityStore(importedFile).save(merged)
        return merged
    }

    private fun readRaw(file: File): JSONObject {
        if (Files.isSymbolicLink(file.toPath())) {
            throw IdentityException("identity.json 不能是符号链接")
        }
        val payload = try {
            readFileLimited(file, MAX_IDENTITY_BYTES)
        } catch (_: InputSizeLimitException) {
            throw IdentityException("identity.json 为空或超过 64 KiB")
        } catch (exc: Exception) {
            throw IdentityException("identity.json 读取失败：${exc.message ?: exc.javaClass.simpleName}")
        }
        if (payload.isEmpty()) throw IdentityException("identity.json 为空或超过 64 KiB")
        return try {
            JSONObject(payload.toString(Charsets.UTF_8))
        } catch (_: Exception) {
            throw IdentityException("identity.json 不是有效 JSON 对象")
        }
    }

    private fun validate(raw: JSONObject, fallbackDisplayName: String): LocalIdentity {
        val userId = requiredId(raw, "user_id", "usr_")
        val deviceId = requiredDeviceId(raw)
        val databaseId = requiredId(raw, "database_id", "db_")
        val profileId = optionalId(raw, "profile_id", "profile_") ?: newId("profile")
        val lastSnapshotId = optionalId(raw, "last_snapshot_id", "snapshot_").orEmpty()
        val displayName = optionalString(raw, "display_name", fallbackDisplayName, 256)
        val createdAt = optionalString(raw, "created_at", "", 64)
        return LocalIdentity(
            userId = userId,
            deviceId = deviceId,
            databaseId = databaseId,
            profileId = profileId,
            lastSnapshotId = lastSnapshotId,
            displayName = displayName,
            createdAt = createdAt,
        )
    }

    private fun requiredId(raw: JSONObject, field: String, prefix: String): String {
        val value = raw.opt(field)
        if (value !is String || !value.startsWith(prefix) || !SAFE_ID.matches(value)) {
            throw IdentityException("identity.json 的 $field 无效")
        }
        return value
    }

    private fun requiredDeviceId(raw: JSONObject): String {
        val value = raw.opt("device_id")
        if (
            value !is String ||
            (!value.startsWith("dev_win_") && !value.startsWith("dev_android_")) ||
            !SAFE_ID.matches(value)
        ) {
            throw IdentityException("identity.json 的 device_id 无效")
        }
        return value
    }

    private fun optionalId(raw: JSONObject, field: String, prefix: String): String? {
        if (!raw.has(field)) return null
        val value = raw.opt(field)
        if (value !is String || value.isEmpty()) return null
        if (!value.startsWith(prefix) || !SAFE_ID.matches(value)) {
            throw IdentityException("identity.json 的 $field 无效")
        }
        return value
    }

    private fun optionalString(
        raw: JSONObject,
        field: String,
        fallback: String,
        maxLength: Int,
    ): String {
        if (!raw.has(field)) return fallback
        val value = raw.opt(field)
        if (value !is String || value.length > maxLength) {
            throw IdentityException("identity.json 的 $field 无效")
        }
        return value
    }
}
