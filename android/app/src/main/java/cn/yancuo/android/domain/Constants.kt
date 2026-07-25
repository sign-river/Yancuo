package cn.yancuo.android.domain

/** Android 新建本地库目前只声明题目核心表基线。 */
const val SCHEMA_VERSION: Int = 7

/** 可安全保留并读取题目核心字段的加法式 Windows 快照上限。 */
const val MAX_EBPACK_SCHEMA_VERSION: Int = 9

/** 跨端题目字段语义版本。 */
const val DATA_FORMAT_VERSION: Int = 1

const val EBPACK_FORMAT: String = "graduate-mistake-book-ebpack"
const val EBPACK_FORMAT_VERSION: Int = 1

const val DEVICE_ID_PREFIX: String = "dev_android_"
