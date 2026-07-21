package cn.yancuo.android.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import cn.yancuo.android.ui.AppViewModel

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun ProblemDetailScreen(
    problemId: String,
    viewModel: AppViewModel,
    onBack: () -> Unit,
) {
    val detail by viewModel.detail.collectAsState()
    LaunchedEffect(problemId) { viewModel.loadDetail(problemId) }

    var title by remember { mutableStateOf("") }
    var question by remember { mutableStateOf("") }
    var answer by remember { mutableStateOf("") }
    var solution by remember { mutableStateOf("") }
    var notes by remember { mutableStateOf("") }
    var tags by remember { mutableStateOf("") }
    var priority by remember { mutableFloatStateOf(3f) }
    var status by remember { mutableStateOf("inbox") }
    var loadedId by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(detail) {
        val d = detail ?: return@LaunchedEffect
        if (d.id != problemId) return@LaunchedEffect
        if (loadedId == d.id) return@LaunchedEffect
        title = d.title.orEmpty()
        question = d.questionMarkdown
        answer = d.correctAnswer
        solution = d.solutionMarkdown
        notes = d.notes
        tags = d.tags.joinToString(", ")
        priority = d.priority.toFloat()
        status = d.status
        loadedId = d.id
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("题目详情") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            OutlinedTextField(
                value = title,
                onValueChange = { title = it },
                label = { Text("标题") },
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = question,
                onValueChange = { question = it },
                label = { Text("题干 (Markdown)") },
                modifier = Modifier.fillMaxWidth(),
                minLines = 3,
            )
            OutlinedTextField(
                value = answer,
                onValueChange = { answer = it },
                label = { Text("正确答案") },
                modifier = Modifier.fillMaxWidth(),
                minLines = 2,
            )
            OutlinedTextField(
                value = solution,
                onValueChange = { solution = it },
                label = { Text("解析") },
                modifier = Modifier.fillMaxWidth(),
                minLines = 2,
            )
            OutlinedTextField(
                value = notes,
                onValueChange = { notes = it },
                label = { Text("备注") },
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = tags,
                onValueChange = { tags = it },
                label = { Text("标签（逗号分隔）") },
                modifier = Modifier.fillMaxWidth(),
            )
            Text("优先级：${priority.toInt()}")
            Slider(
                value = priority,
                onValueChange = { priority = it },
                valueRange = 1f..5f,
                steps = 3,
            )
            Text("状态")
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("inbox" to "收件箱", "active" to "正式", "archived" to "归档").forEach { (value, label) ->
                    FilterChip(
                        selected = status == value,
                        onClick = { status = value },
                        label = { Text(label) },
                    )
                }
            }
            Button(
                onClick = {
                    viewModel.saveDetail(
                        id = problemId,
                        title = title,
                        questionMarkdown = question,
                        correctAnswer = answer,
                        solutionMarkdown = solution,
                        notes = notes,
                        priority = priority.toInt(),
                        status = status,
                        tagsCsv = tags,
                    )
                    loadedId = null
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("保存")
            }
        }
    }
}
