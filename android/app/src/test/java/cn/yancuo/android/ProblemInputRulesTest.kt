package cn.yancuo.android

import cn.yancuo.android.data.repo.MAX_PROBLEM_TAGS
import cn.yancuo.android.data.repo.MAX_PROBLEM_TEXT_FIELD_CHARS
import cn.yancuo.android.data.repo.MAX_PROBLEM_TITLE_CHARS
import cn.yancuo.android.data.repo.MAX_TAG_CSV_CHARS
import cn.yancuo.android.data.repo.ProblemInputException
import cn.yancuo.android.data.repo.normalizeTagNames
import cn.yancuo.android.data.repo.parseTagCsv
import cn.yancuo.android.data.repo.validateProblemTexts
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class ProblemInputRulesTest {
    @Test
    fun validateProblemTexts_acceptsDeclaredTitleBoundary() {
        validateProblemTexts("题".repeat(MAX_PROBLEM_TITLE_CHARS), mapOf("题干" to "内容"))
    }

    @Test
    fun validateProblemTexts_rejectsOversizedTitle() {
        assertThrows(ProblemInputException::class.java) {
            validateProblemTexts("题".repeat(MAX_PROBLEM_TITLE_CHARS + 1), emptyMap())
        }
    }

    @Test
    fun validateProblemTexts_rejectsOversizedField() {
        assertThrows(ProblemInputException::class.java) {
            validateProblemTexts(
                null,
                mapOf("题干" to "x".repeat(MAX_PROBLEM_TEXT_FIELD_CHARS + 1)),
            )
        }
    }

    @Test
    fun normalizeTagNames_trimsAndDeduplicates() {
        assertEquals(listOf("高数", "极限"), normalizeTagNames(listOf(" 高数 ", "极限", "高数")))
    }

    @Test
    fun normalizeTagNames_rejectsTooManyTags() {
        assertThrows(ProblemInputException::class.java) {
            normalizeTagNames((0..MAX_PROBLEM_TAGS).map { "tag-$it" })
        }
    }

    @Test
    fun parseTagCsv_rejectsOversizedRawInputBeforeSplit() {
        assertThrows(ProblemInputException::class.java) {
            parseTagCsv("a".repeat(MAX_TAG_CSV_CHARS + 1))
        }
    }
}
