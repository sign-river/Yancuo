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
import cn.yancuo.android.data.repo.ProblemDetail
import cn.yancuo.android.data.repo.ProblemSummary
import cn.yancuo.android.data.repo.ReviewResult
import cn.yancuo.android.domain.DATA_FORMAT_VERSION
import cn.yancuo.android.domain.SCHEMA_VERSION
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream

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
    val gitLinkToken: String = "",
    val gitHubToken: String = "",
    val hasGitLink: Boolean = false,
    val hasGitHub: Boolean = false,
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

    fun refreshHome() {
        viewModelScope.launch {
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
            _home.update { it.copy(items = items) }
        }
    }

    fun setHomeTab(tab: HomeTab) {
        _home.update { it.copy(tab = tab) }
        refreshHome()
    }

    fun setQuery(query: String) {
        _home.update { it.copy(query = query) }
        refreshHome()
    }

    fun refreshDue() {
        viewModelScope.launch {
            _due.value = withContext(Dispatchers.IO) { app.problems.listDueReviews() }
        }
    }

    fun loadDetail(id: String) {
        viewModelScope.launch {
            _detail.value = withContext(Dispatchers.IO) { app.problems.get(id) }
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
                    tagNames = tagsCsv.split(',', '，', ';', '；', ' ').map { it.trim() },
                )
            }
            loadDetail(id)
            refreshHome()
            _home.update { it.copy(message = "已保存") }
        }
    }

    fun importImages(files: List<File>) {
        viewModelScope.launch {
            _busy.value = true
            try {
                val created = withContext(Dispatchers.IO) {
                    app.problems.createFromImages(files)
                }
                _home.update { it.copy(tab = HomeTab.INBOX, message = "已导入 ${created.size} 题到收件箱") }
                refreshHome()
            } finally {
                _busy.value = false
            }
        }
    }

    fun copyUriToCache(uri: Uri, nameHint: String): File? {
        return try {
            val dir = File(app.cacheDir, "imports").also { it.mkdirs() }
            val dest = File(dir, "${System.currentTimeMillis()}_$nameHint")
            app.contentResolver.openInputStream(uri)?.use { input ->
                FileOutputStream(dest).use { output -> input.copyTo(output) }
            } ?: return null
            dest
        } catch (_: Exception) {
            null
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
            gitLinkToken = "",
            gitHubToken = "",
            hasGitLink = tokens.hasGitLinkToken(),
            hasGitHub = tokens.hasGitHubToken(),
            message = null,
        )
    }

    fun saveTokens(gitLink: String, gitHub: String) {
        if (gitLink.isNotBlank()) app.tokenStore.saveGitLinkToken(gitLink)
        if (gitHub.isNotBlank()) app.tokenStore.saveGitHubToken(gitHub)
        refreshSettings()
        _settings.update { it.copy(message = "Token 已保存（加密存储）") }
    }

    fun clearTokens() {
        app.tokenStore.clearAll()
        refreshSettings()
        _settings.update { it.copy(message = "Token 已清除") }
    }

    fun importEbpack(uri: Uri) {
        viewModelScope.launch {
            _busy.value = true
            try {
                val result: EbpackImportResult = withContext(Dispatchers.IO) {
                    val cache = File(app.paths.cacheDir, "import-${System.currentTimeMillis()}.ebpack")
                    app.contentResolver.openInputStream(uri)?.use { input ->
                        FileOutputStream(cache).use { output -> input.copyTo(output) }
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
                _settings.update { it.copy(message = "导入失败：${e.message}") }
            } finally {
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
