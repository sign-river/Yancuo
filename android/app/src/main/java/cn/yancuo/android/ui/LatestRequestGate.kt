package cn.yancuo.android.ui

import java.util.concurrent.atomic.AtomicLong

internal class LatestRequestGate {
    private val generation = AtomicLong(0)

    fun next(): Long = generation.incrementAndGet()

    fun isCurrent(token: Long): Boolean = generation.get() == token
}
