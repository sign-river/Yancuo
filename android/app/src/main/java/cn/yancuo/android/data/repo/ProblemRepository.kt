package cn.yancuo.android.data.repo

import android.content.ContentValues
import android.database.Cursor
import cn.yancuo.android.data.assets.ObjectStore
import cn.yancuo.android.data.db.YancuoDb
import cn.yancuo.android.domain.REVIEW_GRADES
import cn.yancuo.android.domain.isDue
import cn.yancuo.android.domain.masteryFromGrade
import cn.yancuo.android.domain.newId
import cn.yancuo.android.domain.nextReviewAt
import cn.yancuo.android.domain.validateGrade
import java.io.File
import java.time.Instant

data class ProblemSummary(
    val id: String,
    val title: String?,
    val status: String,
    val priority: Int,
    val notes: String,
    val nextReviewAt: Instant?,
    val reviewCount: Int,
    val mastery: Int?,
)

data class ProblemDetail(
    val id: String,
    val title: String?,
    val status: String,
    val priority: Int,
    val questionMarkdown: String,
    val correctAnswer: String,
    val solutionMarkdown: String,
    val errorAnalysis: String,
    val notes: String,
    val tags: List<String>,
    val nextReviewAt: Instant?,
    val reviewCount: Int,
    val mastery: Int?,
)

data class ReviewResult(
    val problemId: String,
    val grade: Int,
    val label: String,
    val nextReviewAt: Instant,
    val reviewCount: Int,
)

class ProblemRepository(
    private val dbHelper: YancuoDb,
    private val objectStore: ObjectStore,
) {

    fun listProblems(status: String? = null, query: String? = null): List<ProblemSummary> {
        val db = dbHelper.readable()
        val args = mutableListOf<String>()
        val where = buildString {
            append("deleted_at IS NULL")
            if (!status.isNullOrBlank()) {
                append(" AND status = ?")
                args += status
            }
            if (!query.isNullOrBlank()) {
                append(" AND (IFNULL(title,'') LIKE ? OR IFNULL(notes,'') LIKE ? OR IFNULL(question_markdown,'') LIKE ?)")
                val q = "%$query%"
                args += q
                args += q
                args += q
            }
        }
        val sql = """
            SELECT id, title, status, priority, notes, next_review_at, review_count, mastery
            FROM problems
            WHERE $where
            ORDER BY updated_at DESC, created_at DESC
        """.trimIndent()
        return db.rawQuery(sql, args.toTypedArray()).use { c ->
            buildList {
                while (c.moveToNext()) add(c.toSummary())
            }
        }
    }

    fun listDueReviews(): List<ProblemSummary> {
        // 与 Windows 一致：仅正式库（active）中到期的题
        return listProblems(status = "active").filter { isDue(it.nextReviewAt) }
    }

    fun get(id: String): ProblemDetail? {
        val db = dbHelper.readable()
        val problem = db.rawQuery(
            """
            SELECT id, title, status, priority, question_markdown, correct_answer,
                   solution_markdown, error_analysis, notes, next_review_at, review_count, mastery
            FROM problems WHERE id = ? AND deleted_at IS NULL
            """.trimIndent(),
            arrayOf(id),
        ).use { c ->
            if (!c.moveToFirst()) return null
            ProblemDetail(
                id = c.getString(0),
                title = c.getString(1),
                status = c.getString(2),
                priority = c.getInt(3),
                questionMarkdown = c.getString(4) ?: "",
                correctAnswer = c.getString(5) ?: "",
                solutionMarkdown = c.getString(6) ?: "",
                errorAnalysis = c.getString(7) ?: "",
                notes = c.getString(8) ?: "",
                tags = emptyList(),
                nextReviewAt = c.getString(9)?.let { parseInstant(it) },
                reviewCount = c.getInt(10),
                mastery = if (c.isNull(11)) null else c.getInt(11),
            )
        }
        val tags = db.rawQuery(
            """
            SELECT t.name FROM tags t
            INNER JOIN problem_tags pt ON pt.tag_id = t.id
            WHERE pt.problem_id = ?
            ORDER BY t.name
            """.trimIndent(),
            arrayOf(id),
        ).use { c ->
            buildList {
                while (c.moveToNext()) add(c.getString(0))
            }
        }
        return problem.copy(tags = tags)
    }

    /** 将图片写入对象库并创建收件箱题目。 */
    fun createFromImages(imageFiles: List<File>): List<String> {
        if (imageFiles.isEmpty()) return emptyList()
        val db = dbHelper.writable()
        val created = mutableListOf<String>()
        val now = Instant.now().toString()
        db.beginTransaction()
        try {
            for (file in imageFiles) {
                val stored = objectStore.storeCopy(file, role = "original")
                val problemId = newId("problem")
                val title = file.nameWithoutExtension.ifBlank { "未命名" }
                db.insertOrThrow(
                    "problems",
                    null,
                    ContentValues().apply {
                        put("id", problemId)
                        put("status", "inbox")
                        put("title", title)
                        put("question_markdown", "")
                        put("question_latex", "")
                        put("user_answer", "")
                        put("correct_answer", "")
                        put("solution_markdown", "")
                        put("error_analysis", "")
                        put("notes", "")
                        put("priority", 3)
                        put("revision", 1)
                        put("review_count", 0)
                        put("created_at", now)
                        put("updated_at", now)
                    },
                )
                db.insertOrThrow(
                    "assets",
                    null,
                    ContentValues().apply {
                        put("id", newId("asset"))
                        put("problem_id", problemId)
                        put("role", "original")
                        put("sha256", stored.sha256)
                        put("relative_path", stored.relativePath)
                        put("mime_type", stored.mimeType)
                        put("size_bytes", stored.sizeBytes)
                        put("is_immutable", 1)
                        put("created_at", now)
                    },
                )
                created += problemId
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
        return created
    }

    fun updateProblem(
        id: String,
        title: String? = null,
        questionMarkdown: String? = null,
        correctAnswer: String? = null,
        solutionMarkdown: String? = null,
        errorAnalysis: String? = null,
        notes: String? = null,
        priority: Int? = null,
        status: String? = null,
        tagNames: List<String>? = null,
    ) {
        val db = dbHelper.writable()
        val now = Instant.now().toString()
        db.beginTransaction()
        try {
            val cv = ContentValues().apply {
                put("updated_at", now)
                title?.let { put("title", it) }
                questionMarkdown?.let { put("question_markdown", it) }
                correctAnswer?.let { put("correct_answer", it) }
                solutionMarkdown?.let { put("solution_markdown", it) }
                errorAnalysis?.let { put("error_analysis", it) }
                notes?.let { put("notes", it) }
                priority?.let { put("priority", it.coerceIn(1, 5)) }
                status?.let {
                    require(it in setOf("inbox", "active", "archived", "trashed")) {
                        "非法状态"
                    }
                    put("status", it)
                }
            }
            val n = db.update("problems", cv, "id = ?", arrayOf(id))
            require(n == 1) { "题目不存在" }
            if (tagNames != null) {
                replaceTags(db, id, tagNames)
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    fun recordReview(problemId: String, grade: Int): ReviewResult {
        val g = validateGrade(grade)
        val nextAt = nextReviewAt(g)
        val label = REVIEW_GRADES.getValue(g)
        val now = Instant.now()
        val db = dbHelper.writable()
        db.beginTransaction()
        try {
            val cur = db.rawQuery(
                "SELECT review_count, status FROM problems WHERE id = ? AND deleted_at IS NULL",
                arrayOf(problemId),
            )
            val (count, status) = cur.use {
                require(it.moveToFirst()) { "题目不存在" }
                it.getInt(0) to it.getString(1)
            }
            require(status != "trashed") { "回收站题目不可复习" }
            val newCount = count + 1
            val newStatus = if (status == "inbox") "active" else status
            db.update(
                "problems",
                ContentValues().apply {
                    put("mastery", masteryFromGrade(g))
                    put("next_review_at", nextAt.toString())
                    put("review_count", newCount)
                    put("status", newStatus)
                    put("updated_at", now.toString())
                },
                "id = ?",
                arrayOf(problemId),
            )
            db.insertOrThrow(
                "reviews",
                null,
                ContentValues().apply {
                    put("id", newId("rev"))
                    put("problem_id", problemId)
                    put("grade", g)
                    put("label", label)
                    put("reviewed_at", now.toString())
                    put("next_review_at", nextAt.toString())
                },
            )
            db.setTransactionSuccessful()
            return ReviewResult(problemId, g, label, nextAt, newCount)
        } finally {
            db.endTransaction()
        }
    }

    private fun replaceTags(db: android.database.sqlite.SQLiteDatabase, problemId: String, names: List<String>) {
        db.delete("problem_tags", "problem_id = ?", arrayOf(problemId))
        val now = Instant.now().toString()
        for (raw in names.map { it.trim() }.filter { it.isNotEmpty() }.distinct()) {
            var tagId: String? = null
            db.rawQuery("SELECT id FROM tags WHERE name = ?", arrayOf(raw)).use { c ->
                if (c.moveToFirst()) tagId = c.getString(0)
            }
            if (tagId == null) {
                tagId = newId("tag")
                db.insertOrThrow(
                    "tags",
                    null,
                    ContentValues().apply {
                        put("id", tagId)
                        put("name", raw)
                        put("is_system", 0)
                        put("created_at", now)
                    },
                )
            }
            db.insertOrThrow(
                "problem_tags",
                null,
                ContentValues().apply {
                    put("problem_id", problemId)
                    put("tag_id", tagId)
                },
            )
        }
    }

    private fun Cursor.toSummary(): ProblemSummary = ProblemSummary(
        id = getString(0),
        title = getString(1),
        status = getString(2),
        priority = getInt(3),
        notes = getString(4) ?: "",
        nextReviewAt = getString(5)?.let { parseInstant(it) },
        reviewCount = getInt(6),
        mastery = if (isNull(7)) null else getInt(7),
    )
}

/** 兼容 ISO-8601 与 SQLite/SQLAlchemy 常见空格分隔时间戳。 */
private fun parseInstant(raw: String): Instant? {
    val t = raw.trim()
    if (t.isEmpty()) return null
    return runCatching { Instant.parse(t) }.getOrNull()
        ?: runCatching {
            Instant.parse(t.replace(' ', 'T').let { if (it.endsWith("Z") || it.contains('+')) it else "${it}Z" })
        }.getOrNull()
}
