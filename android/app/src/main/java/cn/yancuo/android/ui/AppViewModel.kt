package cn.yancuo.android.ui

import android.app.Application
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import cn.yancuo.android.YancuoApp
import cn.yancuo.android.data.ebpack.EbpackException
import cn.yancuo.android.data.ebpack.EbpackImportResult
import cn.yancuo.android.data.io.MAX_EBPACK_BYTES
import cn.yancuo.android.data.io.copyToFileLimited
import cn.yancuo.android.data.repo.ProblemDetail
import cn.yancuo.android.data.repo.ProblemSummary
import cn.yancuo.android.data.repo.ReviewResult
import cn.yancuo.android.data.repo.parseTagCsv
import cn.yancuo.android.domain.DATA_FORMAT_VERSION
import cn.yancuo.android.domain.SCHEMA_VERSION
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

data class HomeUiState(
    val tab: HomeTab = HomeTab.INBOX,
    val query: String = "",
    val items: List<ProblemSummary> = emptyList(),
    val message: String? = null,
)

enum class HomeTab { INBOX, LIBRARY }

data class SettingsUiState(
    val dataRoot: String = "",
    val schemaVersion: Int = SCHEMA_VERSION,
    val dataFormatVersion: Int = DATA_FORMAT_VERSION,
    val hasCloudBaseToken: Boolean = false,
    val message: String? = null,
)

class AppViewModel(application: Application) : AndroidViewModel(application) {

    private val app get() = getApplication<YancuoApp>()

    private val _home = MutableStateFlow(HomeUiState())
    val home: StateFlow<HomeUiState> = _home.asStateFlow()

    private val _due = MutableStateFlow<List<ProblemSummary>>(emptyList())
    val due: StateFlow<List<ProblemSummary>> = _due.asStateFlow()

    private val _detail = MutableStateFlow<ProblemDetail?>(null)
    val detail: StateFlow<ProblemDetail?> = _detail.asStateFlow()

    private val _settings = MutableStateFlow(SettingsUiState())
    val settings: StateFlow<SettingsUiState> = _settings.asStateFlow()

    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy.asStateFlow()
    private val importGate = ExclusiveOperationGate()
    private val homeRequestGate = LatestRequestGate()
    private val detailRequestGate = LatestRequestGate()
    private var homeRefreshJob: Job? = null
    private var detailLoadJob: Job? = null

    fun refreshHome(debounceMillis: Long = 0) {
        val request = homeRequestGate.next()
        homeRefreshJob?.cancel()
        homeRefreshJob = viewModelScope.launch {
            if (debounceMillis > 0) delay(debounceMillis)
            val state = _home.value
            val status = when (state.tab) {
                HomeTab.INBOX -> "inbox"
                HomeTab.LIBRARY -> null
            }
            val items = withContext(Dispatchers.IO) {
                val all = app.problems.listProblems(status = status, query = state.query.ifBlank { null })
                if (state.tab == HomeTab.LIBRARY) {
                    all.filter { it.status != "inbox" && it.status != "trashed" }
                } else {
                    all
                }
            }
            if (homeRequestGate.isCurrent(request)) {
                _home.update { it.copy(items = items) }
            }
        }
    }

    fun setHomeTab(tab: HomeTab) {
        _home.update { it.copy(tab = tab) }
        refreshHome()
    }

    fun setQuery(query: String) {
        _home.update { it.copy(query = query.take(512)) }
        refreshHome(debounceMillis = 180)
    }

    fun refreshDue() {
        viewModelScope.launch {
            _due.value = withContext(Dispatchers.IO) { app.problems.listDueReviews() }
        }
    }

    fun loadDetail(id: String) {
        val request = detailRequestGate.next()
        detailLoadJob?.cancel()
        detailLoadJob = viewModelScope.launch {
            val loaded = withContext(Dispatchers.IO) { app.problems.get(id) }
            if (detailRequestGate.isCurrent(request)) _detail.value = loaded
        }
    }

    fun saveDetail(
        id: String,
        title: String,
        questionMarkdown: String,
        correctAnswer: String,
        solutionMarkdown: String,
        notes: String,
        priority: Int,
        status: String,
        tagsCsv: String,
    ) {
        viewModelScope.launch {
            val outcome = runCatching {
                val tags = parseTagCsv(tagsCsv)
                withContext(Dispatchers.IO) {
                    app.problems.updateProblem(
                        id = id,
                        title = title,
                        questionMarkdown = questionMarkdown,
                        correctAnswer = correctAnswer,
                        solutionMarkdown = solutionMarkdown,
                        notes = notes,
                        priority = priority,
                        status = status,
                        tagNames = tags,
                    )
                }
            }
            if (outcome.isSuccess) {
                loadDetail(id)
                refreshHome()
                _home.update { it.copy(message = "已保存") }
            } else {
                _home.update { it.copy(message = "保存失败：${outcome.exceptionOrNull()?.message}") }
            }
        }
    }

    fun recordReview(problemId: String, grade: Int, onDone: (ReviewResult) -> Unit) {
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                app.problems.recordReview(problemId, grade)
            }
            refreshDue()
            refreshHome()
            onDone(result)
        }
    }

    fun refreshSettings() {
        val tokens = app.tokenStore
        _settings.value = SettingsUiState(
            dataRoot = app.paths.root.absolutePath,
            schemaVersion = runCatching { app.db.schemaVersion() }.getOrDefault(SCHEMA_VERSION),
            dataFormatVersion = DATA_FORMAT_VERSION,
            hasCloudBaseToken = tokens.hasCloudBaseToken(),
            message = null,
        )
    }

    fun saveToken(cloudBase: String) {
        val outcome = runCatching { app.tokenStore.saveCloudBaseToken(cloudBase) }
        refreshSettings()
        _settings.update {
            it.copy(
                message = outcome.fold(
                    onSuccess = { "Token 已保存（加密存储）" },
                    onFailure = { error -> "Token 保存失败：${error.message}" },
                ),
            )
        }
    }

    fun clearTokens() {
        val outcome = runCatching { app.tokenStore.clearAll() }
        refreshSettings()
        _settings.update {
            it.copy(
                message = outcome.fold(
                    onSuccess = { "Token 已清除" },
                    onFailure = { error -> "Token 清除失败：${error.message}" },
                ),
            )
        }
    }

    fun importEbpack(uri: Uri) {
        if (!importGate.tryEnter()) {
            _settings.update { it.copy(message = "已有备份正在导入，请等待完成") }
            return
        }
        _busy.value = true
        viewModelScope.launch {
            try {
                val result: EbpackImportResult = withContext(Dispatchers.IO) {
                    val cache = File(app.paths.cacheDir, "import-${System.currentTimeMillis()}.ebpack")
                    app.contentResolver.openInputStream(uri)?.use { input ->
                        copyToFileLimited(input, cache, MAX_EBPACK_BYTES)
                    } ?: throw EbpackException("无法读取所选文件")
                    try {
                        val r = app.ebpackImporter.importPack(cache)
                        app.reopenAfterImport()
                        r
                    } finally {
                        cache.delete()
                    }
                }
                refreshSettings()
                refreshHome()
                refreshDue()
                _settings.update {
                    it.copy(
                        message = "导入成功：schema=${result.schemaVersion}，题目约 ${result.problemCount}。" +
                            result.note,
                    )
                }
            } catch (e: Exception) {
                val reopenFailure = runCatching {
                    withContext(Dispatchers.IO) { app.reopenAfterImport() }
                }.exceptionOrNull()
                _settings.update {
                    it.copy(
                        message = if (reopenFailure == null) {
                            "导入失败：${e.message}"
                        } else {
                            "导入失败且本地库重开失败，请重启应用：${e.message}"
                        },
                    )
                }
            } finally {
                importGate.exit()
                _busy.value = false
            }
        }
    }

    fun clearHomeMessage() {
        _home.update { it.copy(message = null) }
    }

    companion object {
        fun factory(app: Application): ViewModelProvider.Factory =
            object : ViewModelProvider.Factory {
                @Suppress("UNCHECKED_CAST")
                override fun <T : ViewModel> create(modelClass: Class<T>): T {
                    return AppViewModel(app) as T
                }
            }
    }
}
