package cn.yancuo.android

import cn.yancuo.android.data.credentials.MAX_CLOUDBASE_TOKEN_CHARS
import cn.yancuo.android.data.credentials.TokenValidationException
import cn.yancuo.android.data.credentials.normalizeCloudBaseToken
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class TokenRulesTest {
    @Test
    fun normalizeCloudBaseToken_trimsOuterWhitespace() {
        assertEquals("header.payload.signature", normalizeCloudBaseToken("  header.payload.signature\n"))
    }

    @Test
    fun normalizeCloudBaseToken_rejectsBlankValue() {
        assertThrows(TokenValidationException::class.java) { normalizeCloudBaseToken(" \n ") }
    }

    @Test
    fun normalizeCloudBaseToken_rejectsEmbeddedWhitespace() {
        assertThrows(TokenValidationException::class.java) {
            normalizeCloudBaseToken("header.payload signature")
        }
    }

    @Test
    fun normalizeCloudBaseToken_rejectsOversizedValue() {
        assertThrows(TokenValidationException::class.java) {
            normalizeCloudBaseToken("a".repeat(MAX_CLOUDBASE_TOKEN_CHARS + 1))
        }
    }
}
