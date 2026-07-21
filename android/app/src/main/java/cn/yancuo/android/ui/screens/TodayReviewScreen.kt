package cn.yancuo.android.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import cn.yancuo.android.domain.REVIEW_GRADES
import cn.yancuo.android.ui.AppViewModel

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun TodayReviewScreen(
    viewModel: AppViewModel,
    onBack: () -> Unit,
    onOpenProblem: (String) -> Unit,
) {
    val due by viewModel.due.collectAsState()
    var feedback by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) { viewModel.refreshDue() }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("今日复习") },
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
                .padding(16.dp),
        ) {
            feedback?.let {
                Text(it, color = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(bottom = 8.dp))
            }
            if (due.isEmpty()) {
                Text("暂无到期题目。请将题目设为「正式」状态后，用复习打分安排下次日期。")
            } else {
                LazyColumn(verticalArrangement = Arrangement.spacedBy(16.dp)) {
                    items(due, key = { it.id }) { item ->
                        Column(modifier = Modifier.fillMaxWidth()) {
                            Text(
                                text = item.title?.ifBlank { null } ?: "（无标题）",
                                style = MaterialTheme.typography.titleMedium,
                                modifier = Modifier
                                    .clickable { onOpenProblem(item.id) }
                                    .padding(bottom = 8.dp),
                            )
                            Text(
                                text = "优先级 ${item.priority} · 已复习 ${item.reviewCount} 次",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            FlowRow(
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                                modifier = Modifier.padding(top = 8.dp),
                            ) {
                                REVIEW_GRADES.forEach { (grade, label) ->
                                    OutlinedButton(
                                        onClick = {
                                            viewModel.recordReview(item.id, grade) { result ->
                                                feedback =
                                                    "已打分 ${result.grade} ${result.label}，下次 ${result.nextReviewAt}"
                                            }
                                        },
                                    ) {
                                        Text("$grade $label")
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
