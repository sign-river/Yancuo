package cn.yancuo.android

import cn.yancuo.android.ui.ExclusiveOperationGate
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ExclusiveOperationGateTest {
    @Test
    fun gate_rejectsConcurrentEntry() {
        val gate = ExclusiveOperationGate()

        assertTrue(gate.tryEnter())
        assertFalse(gate.tryEnter())
    }

    @Test
    fun gate_canBeReusedAfterExit() {
        val gate = ExclusiveOperationGate()

        assertTrue(gate.tryEnter())
        gate.exit()
        assertTrue(gate.tryEnter())
    }
}
