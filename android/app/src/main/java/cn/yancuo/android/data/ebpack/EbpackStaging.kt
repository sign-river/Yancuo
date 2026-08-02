package cn.yancuo.android.data.ebpack

import java.io.File

internal fun stageExtractedEbpack(extractedRoot: File, staging: File) {
    val database = File(extractedRoot, "database/snapshot.sqlite")
    val assets = File(extractedRoot, "assets")
    if (!database.isFile) throw EbpackException("缺少 database/snapshot.sqlite")
    if (!assets.isDirectory) throw EbpackException("缺少 assets 目录")

    staging.mkdirs()
    val moves = mutableListOf(
        database to File(staging, "error_book.db"),
        assets to File(staging, "assets"),
    )
    val identity = File(extractedRoot, "identity.json")
    if (identity.isFile) moves += identity to File(staging, "identity.json")
    moves.forEach { (_, destination) ->
        if (destination.exists()) {
            throw EbpackException("恢复暂存目标已存在：${destination.name}")
        }
    }

    val completed = mutableListOf<Pair<File, File>>()
    try {
        moves.forEach { (source, destination) ->
            destination.parentFile?.mkdirs()
            if (!source.renameTo(destination)) {
                throw EbpackException("无法原子移动恢复载荷：${source.name}")
            }
            completed += source to destination
        }
    } catch (exc: Exception) {
        var rollbackFailed = false
        completed.asReversed().forEach { (source, destination) ->
            source.parentFile?.mkdirs()
            if (!destination.renameTo(source)) rollbackFailed = true
        }
        if (rollbackFailed) {
            throw EbpackException("恢复载荷暂存失败且临时回滚不完整，请清理应用缓存")
        }
        if (exc is EbpackException) throw exc
        throw EbpackException("恢复载荷暂存失败：${exc.message ?: exc.javaClass.simpleName}")
    }
}
