package cn.yancuo.android

import cn.yancuo.android.ui.LatestRequestGate
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LatestRequestGateTest {
    @Test
    fun gate_marksOlderRequestAsStale() {
        val gate = LatestRequestGate()
        val first = gate.next()
        val second = gate.next()

        assertFalse(gate.isCurrent(first))
        assertTrue(gate.isCurrent(second))
    }

    @Test
    fun gate_acceptsOnlyIssuedCurrentToken() {
        val gate = LatestRequestGate()
        val current = gate.next()

        assertFalse(gate.isCurrent(current + 1))
        assertTrue(gate.isCurrent(current))
    }
}
