package cn.yancuo.android.ui

import java.util.concurrent.atomic.AtomicBoolean

internal class ExclusiveOperationGate {
    private val entered = AtomicBoolean(false)

    fun tryEnter(): Boolean = entered.compareAndSet(false, true)

    fun exit() {
        check(entered.compareAndSet(true, false)) { "独占操作门尚未进入" }
    }
}
