package cn.yancuo.android

import cn.yancuo.android.domain.intervalDaysForGrade
import cn.yancuo.android.domain.isDue
import cn.yancuo.android.domain.nextReviewAt
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset

class ReviewRulesTest {

    @Test
    fun intervalDays_matchWindows() {
        assertEquals(1, intervalDaysForGrade(1))
        assertEquals(2, intervalDaysForGrade(2))
        assertEquals(4, intervalDaysForGrade(3))
        assertEquals(7, intervalDaysForGrade(4))
        assertEquals(14, intervalDaysForGrade(5))
    }

    @Test
    fun nextReviewAt_alignsToUtcMidnightPlusInterval() {
        val from = Instant.parse("2026-07-21T15:30:00Z")
        val next = nextReviewAt(4, from) // +7 days
        val date = next.atZone(ZoneOffset.UTC).toLocalDate()
        assertEquals(LocalDate.of(2026, 7, 28), date)
        assertEquals(0, next.atZone(ZoneOffset.UTC).hour)
    }

    @Test
    fun isDue_nullMeansDue() {
        assertTrue(isDue(null, today = LocalDate.of(2026, 7, 21)))
        val future = Instant.parse("2026-07-30T00:00:00Z")
        assertFalse(isDue(future, today = LocalDate.of(2026, 7, 21)))
        val past = Instant.parse("2026-07-20T00:00:00Z")
        assertTrue(isDue(past, today = LocalDate.of(2026, 7, 21)))
    }
}
