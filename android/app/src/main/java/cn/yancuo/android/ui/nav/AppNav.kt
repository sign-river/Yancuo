package cn.yancuo.android.ui.nav

import androidx.compose.runtime.Composable
import androidx.navigation.NavHostController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.navArgument
import cn.yancuo.android.ui.AppViewModel
import cn.yancuo.android.ui.screens.CaptureScreen
import cn.yancuo.android.ui.screens.HomeScreen
import cn.yancuo.android.ui.screens.ProblemDetailScreen
import cn.yancuo.android.ui.screens.SettingsScreen
import cn.yancuo.android.ui.screens.TodayReviewScreen

@Composable
fun AppNav(
    navController: NavHostController,
    viewModel: AppViewModel,
) {
    NavHost(navController = navController, startDestination = Routes.HOME) {
        composable(Routes.HOME) {
            HomeScreen(
                viewModel = viewModel,
                onCapture = { navController.navigate(Routes.CAPTURE) },
                onTodayReview = { navController.navigate(Routes.TODAY_REVIEW) },
                onSettings = { navController.navigate(Routes.SETTINGS) },
                onOpenProblem = { id -> navController.navigate(Routes.problemDetail(id)) },
            )
        }
        composable(Routes.CAPTURE) {
            CaptureScreen(
                viewModel = viewModel,
                onBack = { navController.popBackStack() },
                onDone = {
                    navController.popBackStack(Routes.HOME, inclusive = false)
                },
            )
        }
        composable(Routes.TODAY_REVIEW) {
            TodayReviewScreen(
                viewModel = viewModel,
                onBack = { navController.popBackStack() },
                onOpenProblem = { id -> navController.navigate(Routes.problemDetail(id)) },
            )
        }
        composable(Routes.SETTINGS) {
            SettingsScreen(
                viewModel = viewModel,
                onBack = { navController.popBackStack() },
            )
        }
        composable(
            route = Routes.PROBLEM_DETAIL,
            arguments = listOf(navArgument("problemId") { type = NavType.StringType }),
        ) { entry ->
            val id = entry.arguments?.getString("problemId").orEmpty()
            ProblemDetailScreen(
                problemId = id,
                viewModel = viewModel,
                onBack = { navController.popBackStack() },
            )
        }
    }
}
