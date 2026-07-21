package cn.yancuo.android.ui.screens

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
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
import cn.yancuo.android.ui.AppViewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    viewModel: AppViewModel,
    onBack: () -> Unit,
) {
    val state by viewModel.settings.collectAsState()
    var gitLink by remember { mutableStateOf("") }
    var gitHub by remember { mutableStateOf("") }
    LaunchedEffect(Unit) { viewModel.refreshSettings() }

    val openEbpack = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri ->
        if (uri != null) viewModel.importEbpack(uri)
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("设置") },
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
            Text("数据根", style = MaterialTheme.typography.titleSmall)
            Text(state.dataRoot, style = MaterialTheme.typography.bodySmall)
            Text("schema_version = ${state.schemaVersion}")
            Text("data_format_version = ${state.dataFormatVersion}")

            Text("导入 .ebpack", style = MaterialTheme.typography.titleSmall)
            OutlinedButton(
                onClick = {
                    openEbpack.launch(
                        arrayOf(
                            "application/zip",
                            "application/octet-stream",
                            "*/*",
                        ),
                    )
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("选择 .ebpack 文件")
            }
            Text(
                "将校验 manifest（graduate-mistake-book-ebpack v1、未加密）与 checksums.sha256，然后全量替换本地库。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            Text("云 Token（仅本地加密存储，阶段 I 不自动下载）", style = MaterialTheme.typography.titleSmall)
            OutlinedTextField(
                value = gitLink,
                onValueChange = { gitLink = it },
                label = {
                    Text(
                        if (state.hasGitLink) "GitLink Token（已保存，输入新值覆盖）"
                        else "GitLink Token",
                    )
                },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            OutlinedTextField(
                value = gitHub,
                onValueChange = { gitHub = it },
                label = {
                    Text(
                        if (state.hasGitHub) "GitHub Token（已保存，输入新值覆盖）"
                        else "GitHub Token",
                    )
                },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            Button(
                onClick = {
                    viewModel.saveTokens(gitLink, gitHub)
                    gitLink = ""
                    gitHub = ""
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("保存 Token")
            }
            OutlinedButton(
                onClick = viewModel::clearTokens,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("清除全部 Token")
            }

            state.message?.let {
                Text(it, color = MaterialTheme.colorScheme.primary)
            }
        }
    }
}
