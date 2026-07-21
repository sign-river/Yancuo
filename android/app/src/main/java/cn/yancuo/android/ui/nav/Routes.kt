package cn.yancuo.android.ui.nav

object Routes {
    const val HOME = "home"
    const val CAPTURE = "capture"
    const val TODAY_REVIEW = "today_review"
    const val SETTINGS = "settings"
    const val PROBLEM_DETAIL = "problem/{problemId}"

    fun problemDetail(problemId: String): String = "problem/$problemId"
}
