import { useCallback, useEffect, useRef, useState } from "react";

const SEGMENTS = 6;

/**
 * A draggable "volumizer" for traffic surge — not a fixed spike button, a
 * continuous 1x-6x control the presenter drags live during a demo. Backed
 * by POST /api/sim/{id}/surge, which genuinely raises the simulated request
 * rate (and, past 2x, promotes a batch of cold objects) — this changes real
 * numbers on the charts, it isn't just a cosmetic meter.
 */
export function SurgeFader({
  value, onChange, disabled,
}: {
  value: number;
  onChange: (v: number) => void;
  disabled?: boolean;
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);

  const setFromClientX = useCallback((clientX: number) => {
    const el = trackRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    onChange(+(1 + frac * 5).toFixed(2));
  }, [onChange]);

  useEffect(() => {
    if (!dragging) return;
    const move = (e: MouseEvent) => setFromClientX(e.clientX);
    const up = () => setDragging(false);
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
  }, [dragging, setFromClientX]);

  const frac = (value - 1) / 5;
  const litSegments = Math.round(frac * SEGMENTS);

  return (
    <div className={`surge ${disabled ? "disabled" : ""}`} title="Drag to raise simulated traffic — a live surge, not a preset">
      <div className="surge-label">
        <span>SURGE</span>
        <b>{value.toFixed(1)}x</b>
      </div>
      <div
        className="surge-track"
        ref={trackRef}
        onMouseDown={(e) => {
          if (disabled) return;
          setDragging(true);
          setFromClientX(e.clientX);
        }}
      >
        {Array.from({ length: SEGMENTS }).map((_, i) => (
          <span
            key={i}
            className={`surge-seg ${i < litSegments ? "lit" : ""}`}
            style={i < litSegments ? { background: segColor(i / SEGMENTS) } : undefined}
          />
        ))}
      </div>
    </div>
  );
}

function segColor(t: number): string {
  // green -> amber -> red as the drag goes further right
  if (t < 0.45) return "#5fe0a0";
  if (t < 0.75) return "#f0c14b";
  return "#f28b82";
}
