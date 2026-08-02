package cn.yancuo.android.data.credentials

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * CloudBase 网关 Token 加密存储。
 * **切勿**将 token 写入日志或崩溃上报。
 */
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
        prefs.edit().remove(KEY_LEGACY_GITLINK).remove(KEY_LEGACY_GITHUB).apply()
    }

    fun getCloudBaseToken(): String = prefs.getString(KEY_CLOUDBASE, "") ?: ""

    fun saveCloudBaseToken(token: String) {
        prefs.edit().putString(KEY_CLOUDBASE, token.trim()).apply()
    }

    fun clearAll() {
        prefs.edit().clear().apply()
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
