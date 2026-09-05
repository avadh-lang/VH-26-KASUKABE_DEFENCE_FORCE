import { useEffect, useRef, useState } from "react";

/**
 * Renders `text` but flashes a brief glow/scale pulse whenever it changes —
 * makes a live-updating stat actually *read* as live instead of just
 * silently mutating between polls.
 */
export function AnimatedNumber({ text, className }: { text: string; className?: string }) {
  const [pulse, setPulse] = useState(false);
  const prev = useRef(text);

  useEffect(() => {
    if (prev.current !== text) {
      prev.current = text;
      setPulse(true);
      const t = setTimeout(() => setPulse(false), 420);
      return () => clearTimeout(t);
    }
  }, [text]);

  return <span className={`${className ?? ""} ${pulse ? "pulse-num" : ""}`}>{text}</span>;
}
