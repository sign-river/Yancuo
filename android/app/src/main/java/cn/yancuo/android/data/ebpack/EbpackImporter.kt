package cn.yancuo.android.data.ebpack

import cn.yancuo.android.data.assets.ObjectStore
import cn.yancuo.android.data.db.YancuoDb
import cn.yancuo.android.data.identity.IdentityStore
import cn.yancuo.android.data.identity.IdentityException
import cn.yancuo.android.data.io.InputSizeLimitException
import cn.yancuo.android.data.io.MAX_EBPACK_METADATA_BYTES
import cn.yancuo.android.data.io.readFileLimited
import cn.yancuo.android.data.paths.DataPaths
import cn.yancuo.android.domain.EBPACK_FORMAT
import cn.yancuo.android.domain.EBPACK_FORMAT_VERSION
import cn.yancuo.android.domain.MAX_EBPACK_SCHEMA_VERSION
import org.json.JSONObject
import java.io.File

class EbpackException(message: String) : Exception(message)

data class EbpackImportResult(
    val schemaVersion: Int,
    val problemCount: Int,
    val note: String,
)

/**
 * 导入 `.ebpack` v1：解压、校验 manifest 与 checksums，全量替换数据根。
 * v1 策略：完整替换 DB + assets；identity 若在包内则全量替换。
 */
class EbpackImporter(
    private val paths: DataPaths,
    private val identityStore: IdentityStore,
) {

    fun importPack(packFile: File): EbpackImportResult {
        if (!packFile.isFile) throw EbpackException("ebpack 文件不存在")
        val tmp = File(paths.cacheDir, "ebpack-import-${System.currentTimeMillis()}")
        val staging = File(paths.root, ".ebpack_final_staging")
        val previous = File(paths.root, ".ebpack_previous")
        try {
            if (tmp.exists()) tmp.deleteRecursively()
            if (staging.exists()) staging.deleteRecursively()
            if (previous.exists()) {
                throw EbpackException("检测到上次恢复留下的备份目录，请先人工恢复或清理")
            }
            tmp.mkdirs()
            extractEbpackSafely(packFile, tmp)
            val manifest = validateAndChecksum(tmp)
            val dbSrc = File(tmp, "database/snapshot.sqlite")
            if (!dbSrc.isFile) throw EbpackException("缺少 database/snapshot.sqlite")
            validateSnapshot(dbSrc, manifest)
            val importedIdentity = File(tmp, "identity.json")
            if (importedIdentity.isFile) {
                try {
                    identityStore.prepareImportedForRestore(
                        importedIdentity,
                        manifest.optString("database_id").ifBlank { null },
                        manifest.optString("profile_id").ifBlank { null },
                    )
                } catch (exc: IdentityException) {
                    throw EbpackException("ebpack identity.json 无效：${exc.message}")
                }
            }

            stageExtractedEbpack(tmp, staging)

            YancuoDb.resetInstance()
            previous.mkdirs()
            val movedOld = mutableListOf<Pair<File, File>>()
            val installed = mutableListOf<File>()
            try {
                val stagedIdentity = File(staging, "identity.json")
                val destinations = mutableListOf(paths.database, paths.assetDir)
                if (stagedIdentity.isFile) destinations += paths.identityFile
                for (destination in destinations) {
                    if (destination.exists()) {
                        val backup = File(previous, destination.name)
                        moveWithinDataRoot(destination, backup)
                        movedOld += (destination to backup)
                    }
                }

                val stagedDatabase = File(staging, "error_book.db")
                installed += paths.database
                moveWithinDataRoot(stagedDatabase, paths.database)
                val stagedAssets = File(staging, "assets")
                installed += paths.assetDir
                moveWithinDataRoot(stagedAssets, paths.assetDir)
                if (stagedIdentity.isFile) {
                    installed += paths.identityFile
                    moveWithinDataRoot(stagedIdentity, paths.identityFile)
                }
                paths.ensureDirectories()
                validateSnapshot(paths.database, manifest)
                identityStore.loadOrCreate()
            } catch (exc: Exception) {
                installed.asReversed().forEach { installedPath ->
                    if (installedPath.isDirectory) {
                        installedPath.deleteRecursively()
                    } else {
                        installedPath.delete()
                    }
                }
                movedOld.asReversed().forEach { (destination, backup) ->
                    if (backup.exists()) moveWithinDataRoot(backup, destination)
                }
                paths.ensureDirectories()
                previous.deleteRecursively()
                throw EbpackException("ebpack 恢复失败，已回滚：${exc.message}")
            }
            previous.deleteRecursively()

            val schema = manifest.optInt("schema_version", 0)
            return EbpackImportResult(
                schemaVersion = schema,
                problemCount = manifest.optInt("problem_count", 0),
                note = "已全量替换本地库与资源（导入资料身份并保留本机 device_id）",
            )
        } finally {
            tmp.deleteRecursively()
            staging.deleteRecursively()
        }
    }

    private fun validateAndChecksum(root: File): JSONObject {
        val required = listOf(
            "manifest.json",
            "checksums.sha256",
            "database/snapshot.sqlite",
            "database/migrations.json",
            "assets/index.json",
        )
        for (rel in required) {
            if (!File(root, rel).isFile) throw EbpackException("ebpack 缺少条目：$rel")
        }
        val manifest = JSONObject(readMetadataText(File(root, "manifest.json"), "manifest.json"))
        if (manifest.optString("format") != EBPACK_FORMAT) {
            throw EbpackException("不是研错库 ebpack（format 不匹配）")
        }
        if (manifest.optInt("format_version", 0) != EBPACK_FORMAT_VERSION) {
            throw EbpackException("ebpack format_version 不受支持")
        }
        if (manifest.optBoolean("encrypted", false)) {
            throw EbpackException("v1 尚未实现加密包解密，拒绝导入")
        }
        val pkgSchema = manifest.optInt("schema_version", 0)
        if (pkgSchema <= 0) {
            throw EbpackException("ebpack schema_version 无效")
        }
        if (pkgSchema > MAX_EBPACK_SCHEMA_VERSION) {
            throw EbpackException(
                "包 schema_version=$pkgSchema 高于程序支持的 " +
                    "$MAX_EBPACK_SCHEMA_VERSION，请升级软件",
            )
        }
        verifyChecksums(root, manifest)
        return manifest
    }

    private fun validateSnapshot(database: File, manifest: JSONObject) {
        val manifestSchema = manifest.optInt("schema_version", 0)
        val db = android.database.sqlite.SQLiteDatabase.openDatabase(
            database.absolutePath,
            null,
            android.database.sqlite.SQLiteDatabase.OPEN_READONLY,
        )
        val snapshotSchema = try {
            val integrity = db.rawQuery("PRAGMA integrity_check", null).use { cursor ->
                if (cursor.moveToFirst()) cursor.getString(0) else ""
            }
            if (integrity != "ok") {
                throw EbpackException("snapshot.sqlite 完整性检查失败：$integrity")
            }
            val hasForeignKeyError = db.rawQuery("PRAGMA foreign_key_check", null).use {
                cursor -> cursor.moveToFirst()
            }
            if (hasForeignKeyError) {
                throw EbpackException("snapshot.sqlite 外键检查失败")
            }
            validateRequiredSchema(db, manifestSchema)
            val expectedProblems = manifest.optInt("problem_count", -1)
            if (expectedProblems < 0) {
                throw EbpackException("manifest problem_count 无效")
            }
            val actualProblems = db.rawQuery("SELECT count(*) FROM problems", null).use {
                cursor -> if (cursor.moveToFirst()) cursor.getInt(0) else -1
            }
            if (actualProblems != expectedProblems) {
                throw EbpackException(
                    "manifest problem_count=$expectedProblems 与快照 $actualProblems 不一致",
                )
            }
            db.rawQuery(
                "SELECT value FROM meta_kv WHERE key='schema_version'",
                null,
            ).use { cursor ->
                if (cursor.moveToFirst()) cursor.getString(0).toIntOrNull() ?: 0 else 0
            }
        } catch (exc: EbpackException) {
            throw exc
        } catch (exc: Exception) {
            throw EbpackException("snapshot.sqlite 缺少有效 schema_version")
        } finally {
            db.close()
        }
        if (snapshotSchema != manifestSchema) {
            throw EbpackException(
                "manifest schema_version=$manifestSchema 与快照 $snapshotSchema 不一致",
            )
        }
    }

    private fun validateRequiredSchema(
        db: android.database.sqlite.SQLiteDatabase,
        schemaVersion: Int,
    ) {
        val required = mutableSetOf(
            "meta_kv",
            "subjects",
            "chapters",
            "problems",
            "assets",
            "tags",
            "problem_tags",
            "versions",
        )
        if (schemaVersion >= 2) {
            required += setOf(
                "prompts",
                "ai_jobs",
                "ai_job_items",
                "review_sessions",
                "review_items",
                "audit_logs",
            )
        }
        if (schemaVersion >= 3) required += "sync_operations"
        if (schemaVersion >= 4) required += "problem_origins"
        if (schemaVersion >= 6) {
            required += setOf("intake_sessions", "intake_assets", "intake_candidates")
        }
        if (schemaVersion >= 7) required += "search_documents"
        if (schemaVersion >= 8) {
            required += setOf("note_documents", "note_blocks", "note_assets", "note_tags")
        }
        if (schemaVersion >= 9) {
            required += setOf(
                "note_intake_sessions",
                "note_intake_assets",
                "note_draft_groups",
                "note_draft_blocks",
            )
        }
        if (schemaVersion >= 10) {
            required += setOf("note_collections", "note_collection_documents")
        }
        val actual = mutableSetOf<String>()
        db.rawQuery("SELECT name FROM sqlite_master WHERE type='table'", null).use { cursor ->
            while (cursor.moveToNext()) actual += cursor.getString(0)
        }
        val missing = required - actual
        if (missing.isNotEmpty()) {
            throw EbpackException("snapshot.sqlite 缺少核心表：${missing.sorted().joinToString()}")
        }

        val requiredColumns = mapOf(
            "problems" to setOf(
                "id",
                "status",
                "title",
                "question_markdown",
                "correct_answer",
                "priority",
                "revision",
            ),
            "assets" to setOf("id", "problem_id", "role", "sha256", "relative_path"),
            "tags" to setOf("id", "name"),
            "problem_tags" to setOf("problem_id", "tag_id"),
        )
        for ((table, expectedColumns) in requiredColumns) {
            val actualColumns = mutableSetOf<String>()
            db.rawQuery("PRAGMA table_info($table)", null).use { cursor ->
                val nameIndex = cursor.getColumnIndexOrThrow("name")
                while (cursor.moveToNext()) actualColumns += cursor.getString(nameIndex)
            }
            val missingColumns = expectedColumns - actualColumns
            if (missingColumns.isNotEmpty()) {
                throw EbpackException(
                    "snapshot.sqlite 的 $table 缺少列：${missingColumns.sorted().joinToString()}",
                )
            }
        }
    }

    private fun verifyChecksums(root: File, manifest: JSONObject) {
        val table = File(root, "checksums.sha256")
        val hasher = ObjectStore(File(root, "assets/objects"))
        val checksummed = mutableSetOf<String>()
        for (line in readMetadataText(table, "checksums.sha256").lineSequence()) {
            val trimmed = line.trim()
            if (trimmed.isEmpty() || trimmed.startsWith("#")) continue
            val parts = trimmed.split("  ", limit = 2)
            if (parts.size != 2) throw EbpackException("checksums 行格式错误：${trimmed.take(80)}")
            val expected = parts[0].trim()
            val rel = parts[1].trim()
            if (!checksummed.add(rel)) throw EbpackException("checksums 路径重复：$rel")
            val path = safePackageFile(root, rel)
            if (!path.isFile) throw EbpackException("checksums 引用缺失：$rel")
            val actual = hasher.hashFile(path)
            if (actual != expected) throw EbpackException("校验失败：$rel")
        }

        val required = mutableSetOf(
            "manifest.json",
            "database/snapshot.sqlite",
            "database/migrations.json",
            "assets/index.json",
        )
        if (File(root, "identity.json").isFile) required += "identity.json"
        val index = JSONObject(
            readMetadataText(File(root, "assets/index.json"), "assets/index.json"),
        )
        val objects = index.optJSONArray("objects")
            ?: throw EbpackException("assets/index.json 缺少 objects 数组")
        val indexedObjects = mutableSetOf<String>()
        for (indexPosition in 0 until objects.length()) {
            val item = objects.optJSONObject(indexPosition)
                ?: throw EbpackException("assets/index.json 对象条目无效")
            val relativePath = item.optString("relative_path").replace('\\', '/')
            if (!relativePath.startsWith("objects/")) {
                throw EbpackException("assets/index.json 对象路径无效：$relativePath")
            }
            val packagePath = "assets/$relativePath"
            if (!indexedObjects.add(packagePath)) {
                throw EbpackException("assets/index.json 对象路径重复：$relativePath")
            }
            val objectFile = safePackageFile(root, packagePath)
            if (!objectFile.isFile) {
                throw EbpackException("assets/index.json 引用缺失：$relativePath")
            }
            if (item.optString("sha256") != hasher.hashFile(objectFile)) {
                throw EbpackException("assets/index.json 哈希不匹配：$relativePath")
            }
        }
        val objectRoot = File(root, "assets/objects")
        val actualObjects = if (objectRoot.isDirectory) {
            objectRoot.walkTopDown()
                .filter { it.isFile }
                .map { it.relativeTo(root).invariantSeparatorsPath }
                .toSet()
        } else {
            emptySet()
        }
        if (actualObjects != indexedObjects) {
            throw EbpackException("assets/index.json 与包内对象文件不一致")
        }
        required += indexedObjects
        val missingChecksums = required - checksummed
        if (missingChecksums.isNotEmpty()) {
            throw EbpackException(
                "checksums 未覆盖必要条目：${missingChecksums.sorted().joinToString()}",
            )
        }
        if (
            manifest.optInt("schema_version", 0) >= 9 &&
            manifest.optInt("asset_count", -1) != indexedObjects.size
        ) {
            throw EbpackException("manifest asset_count 与对象索引数量不一致")
        }
    }

    private fun safePackageFile(root: File, relativePath: String): File {
        val canonicalRoot = root.canonicalFile
        val candidate = File(root, relativePath).canonicalFile
        if (!candidate.path.startsWith(canonicalRoot.path + File.separator)) {
            throw EbpackException("非法包内路径：$relativePath")
        }
        return candidate
    }

    private fun readMetadataText(file: File, label: String): String {
        return try {
            readFileLimited(file, MAX_EBPACK_METADATA_BYTES).toString(Charsets.UTF_8)
        } catch (_: InputSizeLimitException) {
            throw EbpackException("$label 超过 8 MiB 上限")
        } catch (exc: Exception) {
            throw EbpackException("$label 读取失败：${exc.message ?: exc.javaClass.simpleName}")
        }
    }

    private fun moveWithinDataRoot(source: File, destination: File) {
        destination.parentFile?.mkdirs()
        if (destination.exists()) {
            throw EbpackException("恢复目标已存在：${destination.name}")
        }
        if (!source.renameTo(destination)) {
            throw EbpackException("无法原子移动：${source.name}")
        }
    }

}
