package cn.yancuo.android

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.rememberNavController
import cn.yancuo.android.ui.AppViewModel
import cn.yancuo.android.ui.nav.AppNav
import cn.yancuo.android.ui.theme.YancuoTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            YancuoTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    val navController = rememberNavController()
                    val vm: AppViewModel = viewModel(
                        factory = AppViewModel.factory(application),
                    )
                    AppNav(navController = navController, viewModel = vm)
                }
            }
        }
    }
}
