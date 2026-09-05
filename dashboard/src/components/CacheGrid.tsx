import { useEffect, useRef, useState } from "react";

export type SampleEntry = {
  key: string; tier: string; size_kb: number; freq: number; pattern: string; value: number;
};
type Displayed = SampleEntry & { phase: "enter" | "stay" | "leave" };

const PATTERN_COLOR: Record<string, string> = {
  periodic: "#6ea8fe", bursty: "#f28b82", random: "#f0c14b", new: "#8b96a5",
};
const TIERS = ["L1", "L2", "L3"] as const;

/**
 * A live view of what's actually sitting in each tier right now — objects
 * fade in when CACHE MIND admits/promotes them and fade out when they're
 * demoted or evicted, straight from CacheMind.sample() each epoch. Nothing
 * here drives a decision; it's purely observational, for the "coming and
 * going" visual.
 */
export function CacheGrid({ sample }: { sample: SampleEntry[] | undefined }) {
  const [items, setItems] = useState<Record<string, Displayed>>({});
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  useEffect(() => {
    const incoming = new Map((sample ?? []).map((e) => [e.key, e]));
    setItems((prev) => {
      const next: Record<string, Displayed> = {};
      for (const [key, entry] of incoming) {
        const was = prev[key];
        const moved = was && was.tier !== entry.tier;
        next[key] = { ...entry, phase: was && !moved ? "stay" : "enter" };
        if (timers.current[key]) { clearTimeout(timers.current[key]); delete timers.current[key]; }
      }
      for (const key of Object.keys(prev)) {
        if (incoming.has(key)) continue;
        if (prev[key].phase === "leave") { next[key] = prev[key]; continue; }
        next[key] = { ...prev[key], phase: "leave" };
        timers.current[key] = setTimeout(() => {
          setItems((cur) => {
            const copy = { ...cur };
            delete copy[key];
            return copy;
          });
        }, 400);
      }
      return next;
    });
  }, [sample]);

  const byTier: Record<string, Displayed[]> = { L1: [], L2: [], L3: [] };
  for (const it of Object.values(items)) (byTier[it.tier] ??= []).push(it);
  for (const t of TIERS) byTier[t]?.sort((a, b) => b.value - a.value);

  return (
    <div className="cachegrid">
      {TIERS.map((t) => (
        <div className="cglane" key={t}>
          <div className="cglabel">{t}<span>{byTier[t]?.filter((i) => i.phase !== "leave").length ?? 0}</span></div>
          <div className="cgchips">
            {(byTier[t] ?? []).map((it) => (
              <div
                key={it.key}
                className={`cgchip ${it.phase}`}
                title={`${it.key} — ${it.size_kb} KB · freq ${it.freq} · ${it.pattern} · value ${it.value}`}
              >
                <span className="cgdot" style={{ background: PATTERN_COLOR[it.pattern] ?? "#8b96a5" }} />
                <span className="cgkey">{shortKey(it.key)}</span>
              </div>
            ))}
            {!byTier[t]?.length && <div className="cgempty">empty</div>}
          </div>
        </div>
      ))}
    </div>
  );
}

function shortKey(k: string): string {
  const parts = k.split(":");
  return parts[parts.length - 1];
}
