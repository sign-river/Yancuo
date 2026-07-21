package cn.yancuo.android.domain

import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.ZonedDateTime

/** 五档：1 完全不会 … 5 完全掌握（与 Windows REVIEW_GRADES 一致）。 */
val REVIEW_GRADES: Map<Int, String> = mapOf(
    1 to "完全不会",
    2 to "有思路但做不出",
    3 to "计算出错",
    4 to "基本正确",
    5 to "完全掌握",
)

private val INTERVAL_DAYS: Map<Int, Int> = mapOf(
    1 to 1,
    2 to 2,
    3 to 4,
    4 to 7,
    5 to 14,
)

fun validateGrade(grade: Int): Int {
    require(grade in REVIEW_GRADES) { "复习结果必须是 1–5" }
    return grade
}

fun intervalDaysForGrade(grade: Int): Int =
    INTERVAL_DAYS.getValue(validateGrade(grade))

/**
 * 根据打分计算下次复习时间（UTC，日期对齐到当天 00:00 + 间隔天）。
 */
fun nextReviewAt(grade: Int, from: Instant = Instant.now()): Instant {
    validateGrade(grade)
    val day = ZonedDateTime.ofInstant(from, ZoneOffset.UTC).toLocalDate()
    val next = day.plusDays(intervalDaysForGrade(grade).toLong())
    return next.atStartOfDay(ZoneOffset.UTC).toInstant()
}

fun masteryFromGrade(grade: Int): Int = validateGrade(grade)

/** 从未复习（null）视为到期。 */
fun isDue(nextReviewAt: Instant?, today: LocalDate = LocalDate.now(ZoneOffset.UTC)): Boolean {
    if (nextReviewAt == null) return true
    val dueDate = ZonedDateTime.ofInstant(nextReviewAt, ZoneOffset.UTC).toLocalDate()
    return !dueDate.isAfter(today)
}
