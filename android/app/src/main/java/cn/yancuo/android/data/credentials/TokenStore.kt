package cn.yancuo.android.data.credentials

import android.annotation.SuppressLint
import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * CloudBase 网关 Token 加密存储。
 * **切勿**将 token 写入日志或崩溃上报。
 */
@SuppressLint("ApplySharedPref") // 凭据操作必须获知加密偏好是否已经同步提交成功。
class TokenStore(context: Context) {

    private val prefs: SharedPreferences

    init {
        val masterKey = MasterKey.Builder(context.applicationContext)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        prefs = EncryptedSharedPreferences.create(
            context.applicationContext,
            PREFS_NAME,
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
        // 旧远端源已经移除，不继续保留其访问令牌。
        prefs.edit().remove(KEY_LEGACY_GITLINK).remove(KEY_LEGACY_GITHUB).commit()
    }

    fun getCloudBaseToken(): String {
        val stored = prefs.getString(KEY_CLOUDBASE, "") ?: ""
        if (stored.isBlank()) return ""
        return try {
            normalizeCloudBaseToken(stored)
        } catch (_: TokenValidationException) {
            prefs.edit().remove(KEY_CLOUDBASE).commit()
            ""
        }
    }

    fun saveCloudBaseToken(token: String) {
        val normalized = normalizeCloudBaseToken(token)
        if (!prefs.edit().putString(KEY_CLOUDBASE, normalized).commit()) {
            throw TokenValidationException("CloudBase Token 加密存储失败")
        }
    }

    fun clearAll() {
        if (!prefs.edit().clear().commit()) {
            throw TokenValidationException("CloudBase Token 清除失败")
        }
    }

    /** 仅用于 UI 展示是否已保存，不返回明文。 */
    fun hasCloudBaseToken(): Boolean = getCloudBaseToken().isNotBlank()

    companion object {
        private const val PREFS_NAME = "yancuo_secure_tokens"
        private const val KEY_CLOUDBASE = "cloudbase_gateway_token"
        private const val KEY_LEGACY_GITLINK = "gitlink_token"
        private const val KEY_LEGACY_GITHUB = "github_token"
    }
}

internal const val MAX_CLOUDBASE_TOKEN_CHARS = 16 * 1024

class TokenValidationException(message: String) : Exception(message)

internal fun normalizeCloudBaseToken(token: String): String {
    val normalized = token.trim()
    if (normalized.isEmpty()) throw TokenValidationException("CloudBase Token 不能为空")
    if (normalized.length > MAX_CLOUDBASE_TOKEN_CHARS) {
        throw TokenValidationException("CloudBase Token 超过 16 KiB 上限")
    }
    if (normalized.any { it.code <= 0x20 || it.code == 0x7F }) {
        throw TokenValidationException("CloudBase Token 不能包含空白或控制字符")
    }
    return normalized
}
