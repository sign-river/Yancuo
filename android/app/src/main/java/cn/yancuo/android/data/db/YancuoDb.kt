package cn.yancuo.android.data.db

import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import cn.yancuo.android.domain.DATA_FORMAT_VERSION
import cn.yancuo.android.domain.SCHEMA_VERSION
import java.io.File

/**
 * 与 Windows SQLAlchemy 模型对齐的核心表。
 *
 * OpenHelper 的 [DATABASE_VERSION] 仅用于全新创建；导入的 Windows DB
 * 通过 [openExistingOrCreate] 用 [SQLiteDatabase.openDatabase] 打开，跳过重建。
 */
class YancuoDb private constructor(
    context: Context,
    private val dbFile: File,
) : SQLiteOpenHelper(context, dbFile.absolutePath, null, DATABASE_VERSION) {

    override fun onCreate(db: SQLiteDatabase) {
        createCoreTables(db)
        db.execSQL(
            "INSERT OR REPLACE INTO meta_kv(key, value) VALUES('schema_version', ?)",
            arrayOf(SCHEMA_VERSION.toString()),
        )
        db.execSQL(
            "INSERT OR REPLACE INTO meta_kv(key, value) VALUES('data_format_version', ?)",
            arrayOf(DATA_FORMAT_VERSION.toString()),
        )
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        // 阶段 I：加法迁移由 ensureMinimalSchema 处理；不 drop。
        ensureMinimalSchema(db)
    }

    fun readable(): SQLiteDatabase = readableDatabase

    fun writable(): SQLiteDatabase = writableDatabase

    fun schemaVersion(): Int {
        val db = readable()
        return try {
            db.rawQuery("SELECT value FROM meta_kv WHERE key='schema_version'", null).use { c ->
                if (c.moveToFirst()) c.getString(0).toIntOrNull() ?: 0 else 0
            }
        } catch (_: Exception) {
            0
        }
    }

    companion object {
        /** OpenHelper 内部版本；与 meta_kv.schema_version 分离。 */
        const val DATABASE_VERSION: Int = 1

        @Volatile
        private var instance: YancuoDb? = null

        /**
         * 若 [dbFile] 已存在（例如 ebpack 恢复），用 openDatabase 探测后仍走 OpenHelper
         * 打开同一路径；onCreate 不会对已有文件触发。
         */
        fun openExistingOrCreate(context: Context, dbFile: File): YancuoDb {
            dbFile.parentFile?.mkdirs()
            if (dbFile.isFile) {
                // 探测是否为有效 SQLite；损坏则抛错，避免静默重建覆盖导入数据
                val probe = SQLiteDatabase.openDatabase(
                    dbFile.absolutePath,
                    null,
                    SQLiteDatabase.OPEN_READWRITE,
                )
                try {
                    ensureMinimalSchema(probe)
                } finally {
                    probe.close()
                }
            }
            return synchronized(this) {
                instance?.takeIf { it.dbFile.absolutePath == dbFile.absolutePath }
                    ?: YancuoDb(context.applicationContext, dbFile).also {
                        // 触发打开；新库走 onCreate
                        it.writable().let { db -> ensureMinimalSchema(db) }
                        instance = it
                    }
            }
        }

        fun resetInstance() {
            synchronized(this) {
                instance?.close()
                instance = null
            }
        }

        fun createCoreTables(db: SQLiteDatabase) {
            db.execSQL(
                """
                CREATE TABLE IF NOT EXISTS meta_kv (
                  key TEXT PRIMARY KEY NOT NULL,
                  value TEXT NOT NULL
                )
                """.trimIndent(),
            )
            db.execSQL(
                """
                CREATE TABLE IF NOT EXISTS subjects (
                  id TEXT PRIMARY KEY NOT NULL,
                  name TEXT NOT NULL UNIQUE,
                  sort_order INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT,
                  updated_at TEXT
                )
                """.trimIndent(),
            )
            db.execSQL(
                """
                CREATE TABLE IF NOT EXISTS chapters (
                  id TEXT PRIMARY KEY NOT NULL,
                  subject_id TEXT NOT NULL,
                  parent_id TEXT,
                  name TEXT NOT NULL,
                  sort_order INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT,
                  updated_at TEXT,
                  FOREIGN KEY(subject_id) REFERENCES subjects(id)
                )
                """.trimIndent(),
            )
            db.execSQL(
                """
                CREATE TABLE IF NOT EXISTS problems (
                  id TEXT PRIMARY KEY NOT NULL,
                  status TEXT NOT NULL DEFAULT 'inbox',
                  subject_id TEXT,
                  chapter_id TEXT,
                  problem_type TEXT,
                  title TEXT,
                  question_markdown TEXT NOT NULL DEFAULT '',
                  question_latex TEXT NOT NULL DEFAULT '',
                  user_answer TEXT NOT NULL DEFAULT '',
                  correct_answer TEXT NOT NULL DEFAULT '',
                  solution_markdown TEXT NOT NULL DEFAULT '',
                  error_analysis TEXT NOT NULL DEFAULT '',
                  notes TEXT NOT NULL DEFAULT '',
                  source_book TEXT,
                  source_year TEXT,
                  page_number TEXT,
                  original_number TEXT,
                  priority INTEGER NOT NULL DEFAULT 3,
                  difficulty INTEGER,
                  mastery INTEGER,
                  is_favorite INTEGER NOT NULL DEFAULT 0,
                  needs_redo INTEGER NOT NULL DEFAULT 0,
                  allow_print INTEGER NOT NULL DEFAULT 1,
                  human_confirmed INTEGER NOT NULL DEFAULT 0,
                  revision INTEGER NOT NULL DEFAULT 1,
                  created_at TEXT,
                  updated_at TEXT,
                  deleted_at TEXT,
                  next_review_at TEXT,
                  review_count INTEGER NOT NULL DEFAULT 0
                )
                """.trimIndent(),
            )
            db.execSQL(
                """
                CREATE TABLE IF NOT EXISTS assets (
                  id TEXT PRIMARY KEY NOT NULL,
                  problem_id TEXT,
                  role TEXT NOT NULL,
                  sha256 TEXT NOT NULL,
                  relative_path TEXT NOT NULL,
                  mime_type TEXT,
                  size_bytes INTEGER,
                  width INTEGER,
                  height INTEGER,
                  is_immutable INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT,
                  FOREIGN KEY(problem_id) REFERENCES problems(id)
                )
                """.trimIndent(),
            )
            db.execSQL(
                """
                CREATE TABLE IF NOT EXISTS tags (
                  id TEXT PRIMARY KEY NOT NULL,
                  name TEXT NOT NULL UNIQUE,
                  color TEXT,
                  parent_id TEXT,
                  is_system INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT
                )
                """.trimIndent(),
            )
            db.execSQL(
                """
                CREATE TABLE IF NOT EXISTS problem_tags (
                  problem_id TEXT NOT NULL,
                  tag_id TEXT NOT NULL,
                  PRIMARY KEY(problem_id, tag_id),
                  FOREIGN KEY(problem_id) REFERENCES problems(id),
                  FOREIGN KEY(tag_id) REFERENCES tags(id)
                )
                """.trimIndent(),
            )
            // 复习历史（Windows 将打分写在 problems 上；本表作安卓侧最小审计）
            db.execSQL(
                """
                CREATE TABLE IF NOT EXISTS reviews (
                  id TEXT PRIMARY KEY NOT NULL,
                  problem_id TEXT NOT NULL,
                  grade INTEGER NOT NULL,
                  label TEXT,
                  reviewed_at TEXT NOT NULL,
                  next_review_at TEXT,
                  FOREIGN KEY(problem_id) REFERENCES problems(id)
                )
                """.trimIndent(),
            )
            db.execSQL("CREATE INDEX IF NOT EXISTS ix_assets_sha256 ON assets(sha256)")
            db.execSQL("CREATE INDEX IF NOT EXISTS ix_problems_status ON problems(status)")
        }

        /** 对已导入库补齐缺失的核心表（不删数据）。 */
        fun ensureMinimalSchema(db: SQLiteDatabase) {
            createCoreTables(db)
            val hasSchema = db.rawQuery(
                "SELECT 1 FROM meta_kv WHERE key='schema_version' LIMIT 1",
                null,
            ).use { it.moveToFirst() }
            if (!hasSchema) {
                db.execSQL(
                    "INSERT INTO meta_kv(key, value) VALUES('schema_version', ?)",
                    arrayOf(SCHEMA_VERSION.toString()),
                )
            }
        }
    }
}
