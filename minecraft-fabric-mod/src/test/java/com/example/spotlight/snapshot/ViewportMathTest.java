package com.example.spotlight.snapshot;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class ViewportMathTest {
    @Test
    void wireValueIsClampedToUnitInterval() {
        assertEquals(0.0, ViewportMath.toWireViewportCenterDistance(-0.2));
        assertEquals(0.5, ViewportMath.toWireViewportCenterDistance(0.5));
        assertEquals(1.0, ViewportMath.toWireViewportCenterDistance(1.0));
        assertEquals(1.0, ViewportMath.toWireViewportCenterDistance(3.273));
    }

    @Test
    void nonFiniteValuesAreRejected() {
        assertThrows(
                IllegalArgumentException.class,
                () -> ViewportMath.toWireViewportCenterDistance(Double.NaN)
        );
    }
}
