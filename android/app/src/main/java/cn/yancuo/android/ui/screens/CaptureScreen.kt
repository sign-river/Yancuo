package cn.yancuo.android.ui.screens

import android.Manifest
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import cn.yancuo.android.ui.AppViewModel
import java.io.File

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CaptureScreen(
    viewModel: AppViewModel,
    onBack: () -> Unit,
    onDone: () -> Unit,
) {
    val context = LocalContext.current
    var cameraFile by remember { mutableStateOf<File?>(null) }
    var status by remember { mutableStateOf("选择拍照或从相册导入，将创建收件箱题目。") }
    var pendingGallery by remember { mutableStateOf(false) }

    val takePicture = rememberLauncherForActivityResult(
        ActivityResultContracts.TakePicture(),
    ) { ok ->
        if (ok) {
            val file = cameraFile
            if (file != null && file.isFile) {
                viewModel.importImages(listOf(file))
                status = "已导入拍照图片"
                onDone()
            }
        } else {
            status = "已取消拍照"
        }
    }

    val pickImages = rememberLauncherForActivityResult(
        ActivityResultContracts.GetMultipleContents(),
    ) { uris ->
        if (uris.isEmpty()) {
            status = "未选择图片"
            return@rememberLauncherForActivityResult
        }
        val files = uris.mapNotNull { uri ->
            val name = uri.lastPathSegment?.substringAfterLast('/') ?: "image.jpg"
            viewModel.copyUriToCache(uri, name)
        }
        if (files.isNotEmpty()) {
            viewModel.importImages(files)
            status = "已导入 ${files.size} 张图片"
            onDone()
        } else {
            status = "无法读取所选图片"
        }
    }

    val galleryPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted && pendingGallery) {
            pickImages.launch("image/*")
        } else if (!granted) {
            status = "未授予相册权限"
        }
        pendingGallery = false
    }

    val cameraPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            startCamera(context, takePicture) { file -> cameraFile = file }
        } else {
            status = "未授予相机权限"
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("采集") },
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
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text(status)
            Button(
                onClick = {
                    val granted = ContextCompat.checkSelfPermission(
                        context,
                        Manifest.permission.CAMERA,
                    ) == PackageManager.PERMISSION_GRANTED
                    if (granted) {
                        startCamera(context, takePicture) { file -> cameraFile = file }
                    } else {
                        cameraPermission.launch(Manifest.permission.CAMERA)
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("拍照")
            }
            OutlinedButton(
                onClick = {
                    val permission = if (Build.VERSION.SDK_INT >= 33) {
                        Manifest.permission.READ_MEDIA_IMAGES
                    } else {
                        Manifest.permission.READ_EXTERNAL_STORAGE
                    }
                    if (ContextCompat.checkSelfPermission(context, permission) ==
                        PackageManager.PERMISSION_GRANTED
                    ) {
                        pickImages.launch("image/*")
                    } else {
                        pendingGallery = true
                        galleryPermission.launch(permission)
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("从相册选择")
            }
        }
    }
}

private fun startCamera(
    context: android.content.Context,
    takePicture: androidx.activity.result.ActivityResultLauncher<Uri>,
    onFile: (File) -> Unit,
) {
    val dir = File(context.cacheDir, "camera").also { it.mkdirs() }
    val file = File(dir, "capture_${System.currentTimeMillis()}.jpg")
    val uri = FileProvider.getUriForFile(
        context,
        "${context.packageName}.fileprovider",
        file,
    )
    onFile(file)
    takePicture.launch(uri)
}
