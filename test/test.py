# SPDX-FileCopyrightText: © 2026 Jason Hickey
# SPDX-License-Identifier: Apache-2.0
#
# NDF smoke test — design-specific properties, not the template demo:
#   1. uio_oe is the static D6 direction mask 0b1011_0011, always.
#   2. After a sof re-align, `valid` (uio[7]) is LOW for header cycles
#      0..5 and HIGH for payload cycles 6..13 of every 14-cycle frame.
#   3. phase_o (uio[1:0]) free-runs mod 4 every clock (the core's
#      4-phase memory-bus strobe).
# Functional (MAC/routing) correctness is certified upstream (Lean
# kernel on the model, SAT on the emitted netlist) and exercised by
# the offline-tape bench; this test checks the live wrapper wiring.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

UIO_OE_EXPECTED = 0b1011_0011
FRAME = 14
HDR = 6


@cocotb.test()
async def test_ndf_wrapper(dut):
    dut._log.info("Start — 55 ns clock (18.18 MHz design point)")
    clock = Clock(dut.clk, 55, unit="ns")
    cocotb.start_soon(clock.start())

    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    # 1. Static direction mask, checked repeatedly through the test.
    assert dut.uio_oe.value == UIO_OE_EXPECTED, (
        f"uio_oe {dut.uio_oe.value} != {UIO_OE_EXPECTED:#010b}"
    )

    # sof pulse on uio[6] re-aligns the frame counter (non-destructive).
    dut.uio_in.value = 1 << 6
    await ClockCycles(dut.clk, 1)
    dut.uio_in.value = 0

    # Derive the frame origin from the chip's own observable rather
    # than offset arithmetic: valid is high for cycles 6..13, so its
    # falling edge marks cycle 0 of the next frame.
    def rd_valid():
        return (dut.uio_out.value.to_unsigned() >> 7) & 1
    for _ in range(3 * FRAME):
        await ClockCycles(dut.clk, 1)
        if rd_valid() == 1:
            break
    assert rd_valid() == 1, "valid never rose after sof"
    for _ in range(FRAME):
        await ClockCycles(dut.clk, 1)
        if rd_valid() == 0:
            break
    assert rd_valid() == 0, "valid never fell — no frame boundary"
    # We are now at some cycle in 0..5 of a frame; advance to the
    # observed boundary precisely: wait for the next rise (cycle 6),
    # then 8 payload cycles later cycle 0 begins.
    rises = 0
    for _ in range(FRAME):
        await ClockCycles(dut.clk, 1)
        if rd_valid() == 1:
            rises = 1
            break
    assert rises == 1, "valid did not rise again"
    await ClockCycles(dut.clk, 8)  # payload cycles 6..13 complete

    # 2+3. Walk two full frames cycle-by-cycle from the observed origin.
    prev_phase = None
    for frame in range(2):
        for cyc in range(FRAME):
            uio = dut.uio_out.value.to_unsigned()
            valid = (uio >> 7) & 1
            expected_valid = 1 if cyc >= HDR else 0
            assert valid == expected_valid, (
                f"frame {frame} cycle {cyc}: valid={valid}, "
                f"expected {expected_valid}"
            )
            assert dut.uio_oe.value == UIO_OE_EXPECTED
            phase = uio & 0b11
            if prev_phase is not None:
                assert phase == (prev_phase + 1) % 4, (
                    f"phase_o {phase} does not follow {prev_phase}"
                )
            prev_phase = phase
            await ClockCycles(dut.clk, 1)

    dut._log.info("NDF wrapper smoke test passed: oe mask, frame "
                  "validity window, and phase strobe all as designed")
