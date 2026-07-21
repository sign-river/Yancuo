package cn.yancuo.android.data.credentials

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * GitLink / GitHub Token 加密存储。
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
    }

    fun getGitLinkToken(): String = prefs.getString(KEY_GITLINK, "") ?: ""

    fun getGitHubToken(): String = prefs.getString(KEY_GITHUB, "") ?: ""

    fun saveGitLinkToken(token: String) {
        prefs.edit().putString(KEY_GITLINK, token.trim()).apply()
    }

    fun saveGitHubToken(token: String) {
        prefs.edit().putString(KEY_GITHUB, token.trim()).apply()
    }

    fun clearGitLinkToken() {
        prefs.edit().remove(KEY_GITLINK).apply()
    }

    fun clearGitHubToken() {
        prefs.edit().remove(KEY_GITHUB).apply()
    }

    fun clearAll() {
        prefs.edit().clear().apply()
    }

    /** 仅用于 UI 展示是否已保存，不返回明文。 */
    fun hasGitLinkToken(): Boolean = getGitLinkToken().isNotBlank()

    fun hasGitHubToken(): Boolean = getGitHubToken().isNotBlank()

    companion object {
        private const val PREFS_NAME = "yancuo_secure_tokens"
        private const val KEY_GITLINK = "gitlink_token"
        private const val KEY_GITHUB = "github_token"
    }
}
