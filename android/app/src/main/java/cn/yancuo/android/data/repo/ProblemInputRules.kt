package cn.yancuo.android.data.repo

internal const val MAX_PROBLEM_TITLE_CHARS = 256
internal const val MAX_PROBLEM_TEXT_FIELD_CHARS = 1024 * 1024
internal const val MAX_PROBLEM_TEXT_TOTAL_CHARS = 4 * 1024 * 1024
internal const val MAX_PROBLEM_TAG_CHARS = 128
internal const val MAX_PROBLEM_TAGS = 100
internal const val MAX_TAG_CSV_CHARS = 16 * 1024

class ProblemInputException(message: String) : Exception(message)

internal fun validateProblemTexts(
    title: String?,
    fields: Map<String, String?>,
) {
    if (title != null && title.length > MAX_PROBLEM_TITLE_CHARS) {
        throw ProblemInputException("标题不能超过 $MAX_PROBLEM_TITLE_CHARS 个字符")
    }
    var total = 0L
    fields.forEach { (label, value) ->
        if (value == null) return@forEach
        if (value.length > MAX_PROBLEM_TEXT_FIELD_CHARS) {
            throw ProblemInputException("$label 不能超过 1 Mi 个字符")
        }
        total += value.length
        if (total > MAX_PROBLEM_TEXT_TOTAL_CHARS) {
            throw ProblemInputException("本次保存的正文内容总量不能超过 4 Mi 个字符")
        }
    }
}

internal fun normalizeTagNames(names: List<String>): List<String> {
    val normalized = names.map { it.trim() }.filter { it.isNotEmpty() }.distinct()
    if (normalized.size > MAX_PROBLEM_TAGS) {
        throw ProblemInputException("单题标签不能超过 $MAX_PROBLEM_TAGS 个")
    }
    normalized.forEach { name ->
        if (name.length > MAX_PROBLEM_TAG_CHARS) {
            throw ProblemInputException("标签名称不能超过 $MAX_PROBLEM_TAG_CHARS 个字符")
        }
        if (name.any { it.code < 0x20 || it.code == 0x7F }) {
            throw ProblemInputException("标签名称不能包含控制字符")
        }
    }
    return normalized
}

internal fun parseTagCsv(raw: String): List<String> {
    if (raw.length > MAX_TAG_CSV_CHARS) {
        throw ProblemInputException("标签输入总长度不能超过 16 Ki 个字符")
    }
    return normalizeTagNames(raw.split(',', '，', ';', '；', ' '))
}
