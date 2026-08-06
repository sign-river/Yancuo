package cn.yancuo.android.data.ebpack

import cn.yancuo.android.data.paths.DataPaths
import java.io.File
import java.io.FileOutputStream

private const val RESTORE_PLAN_FILE = ".restore-plan-v1"

internal fun createEbpackRestorePlan(previous: File, destinations: List<File>) {
    if (previous.exists()) throw EbpackException("恢复备份目录已存在")
    if (!previous.mkdirs()) throw EbpackException("无法创建恢复备份目录")
    val installNames = destinations.map { it.name }.toSet()
    require(installNames.all { it in setOf("error_book.db", "assets", "identity.json") }) {
        "恢复计划包含未知目标"
    }
    val oldNames = destinations.filter { it.exists() }.map { it.name }.toSet()
    val payload = buildString {
        appendLine("version=1")
        installNames.sorted().forEach { appendLine("install=$it") }
        oldNames.sorted().forEach { appendLine("old=$it") }
    }.toByteArray(Charsets.UTF_8)
    val plan = File(previous, RESTORE_PLAN_FILE)
    val temporary = File(previous, "$RESTORE_PLAN_FILE.tmp")
    try {
        FileOutputStream(temporary).use { output ->
            output.write(payload)
            output.flush()
            output.fd.sync()
        }
        if (!temporary.renameTo(plan)) throw EbpackException("无法提交恢复回滚清单")
    } finally {
        temporary.delete()
    }
}

internal fun recoverInterruptedEbpack(paths: DataPaths): Boolean {
    val staging = File(paths.root, ".ebpack_final_staging")
    val previous = File(paths.root, ".ebpack_previous")
    if (!previous.exists()) {
        if (staging.exists() && !staging.deleteRecursively()) {
            throw EbpackException("无法清理未启用的恢复暂存目录")
        }
        return false
    }

    val planFile = File(previous, RESTORE_PLAN_FILE)
    if (!planFile.isFile) {
        recoverLegacyBackups(paths, previous)
        staging.deleteRecursively()
        previous.deleteRecursively()
        paths.ensureDirectories()
        return true
    }

    val (installNames, oldNames) = readRestorePlan(planFile)
    val destinations = mapOf(
        "error_book.db" to paths.database,
        "assets" to paths.assetDir,
        "identity.json" to paths.identityFile,
    )
    installNames.forEach { name ->
        val destination = destinations.getValue(name)
        val backup = File(previous, name)
        when {
            backup.exists() -> {
                deletePath(destination)
                destination.parentFile?.mkdirs()
                if (!backup.renameTo(destination)) {
                    throw EbpackException("无法恢复上次资料：$name")
                }
            }
            name in oldNames -> {
                if (!destination.exists()) {
                    throw EbpackException("上次恢复的旧资料与目标均缺失：$name")
                }
            }
            else -> deletePath(destination)
        }
    }
    if (!previous.deleteRecursively()) throw EbpackException("无法清理恢复备份目录")
    if (staging.exists() && !staging.deleteRecursively()) {
        throw EbpackException("无法清理恢复暂存目录")
    }
    paths.ensureDirectories()
    return true
}

private fun readRestorePlan(plan: File): Pair<Set<String>, Set<String>> {
    if (plan.length() !in 1..4096) throw EbpackException("恢复回滚清单大小无效")
    val lines = plan.readLines(Charsets.UTF_8)
    if (lines.firstOrNull() != "version=1") throw EbpackException("恢复回滚清单版本无效")
    val install = lines.mapNotNull { it.removePrefix("install=").takeIf { value -> value != it } }.toSet()
    val old = lines.mapNotNull { it.removePrefix("old=").takeIf { value -> value != it } }.toSet()
    val allowed = setOf("error_book.db", "assets", "identity.json")
    if (install.isEmpty() || install.any { it !in allowed } || old.any { it !in install }) {
        throw EbpackException("恢复回滚清单内容无效")
    }
    return install to old
}

private fun recoverLegacyBackups(paths: DataPaths, previous: File) {
    val destinations = listOf(paths.database, paths.assetDir, paths.identityFile)
    destinations.forEach { destination ->
        val backup = File(previous, destination.name)
        if (backup.exists()) {
            deletePath(destination)
            destination.parentFile?.mkdirs()
            if (!backup.renameTo(destination)) {
                throw EbpackException("无法恢复旧版中断备份：${destination.name}")
            }
        }
    }
}

private fun deletePath(path: File) {
    if (!path.exists()) return
    val deleted = if (path.isDirectory) path.deleteRecursively() else path.delete()
    if (!deleted) throw EbpackException("无法移除中断恢复的临时目标：${path.name}")
}
