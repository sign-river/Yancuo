package cn.yancuo.android.domain

import java.util.UUID

/** 生成带前缀的 ID，例如 `problem_…`、`asset_…`。 */
fun newId(prefix: String): String = "${prefix}_${UUID.randomUUID().toString().replace("-", "")}"

/** 安卓设备 ID：`dev_android_` + hex。 */
fun newDeviceId(): String = DEVICE_ID_PREFIX + UUID.randomUUID().toString().replace("-", "")
