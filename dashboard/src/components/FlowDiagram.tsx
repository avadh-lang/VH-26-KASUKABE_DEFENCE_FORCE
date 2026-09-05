/**
 * A live traffic-flow visualisation — not decoration. Particle density and
 * speed on each branch are driven by the real l1/l2/l3/miss rates for this
 * epoch, so watching it *is* watching the hit-rate numbers, just rendered
 * as motion instead of a percentage.
 *
 * `running` matters: the <animateMotion> elements below loop forever on
 * their own, with no idea whether the simulation behind them is still
 * producing frames — without gating them, stopping the sim would leave
 * particles flying past a frozen last-known rate forever. (SVGSVGElement's
 * pauseAnimations()/unpauseAnimations() looks like the right tool for this
 * but proved unreliable across mount/remount in testing — resume silently
 * didn't resume. Simplest robust fix: just don't render the animated
 * particles at all while stopped. Paths/labels stay, dimmed via the
 * `.stopped` class, so the last state is still visible — just clearly inert.)
 */
type Branch = { id: string; label: string; sub: string; y: number; rate: number; color: string };

export function FlowDiagram({
  l1, l2, l3, miss, running,
}: {
  l1: number; l2: number; l3: number; miss: number; running: boolean;
}) {
  const branches: Branch[] = [
    { id: "l1", label: "L1", sub: "0.5ms", y: 34, rate: l1, color: "#f28b82" },
    { id: "l2", label: "L2", sub: "4ms", y: 84, rate: l2, color: "#6ea8fe" },
    { id: "l3", label: "L3", sub: "28ms", y: 134, rate: l3, color: "#5fe0a0" },
    { id: "origin", label: "ORIGIN", sub: "miss", y: 184, rate: miss, color: "#f0c14b" },
  ];

  return (
    <svg
      viewBox="0 0 560 218"
      className={`flowdiagram ${running ? "" : "stopped"}`}
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        <filter id="fdglow" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      {/* inbound requests -> CACHE MIND */}
      <path id="fd-in" d="M14 109 L150 109" stroke="#5d6674" strokeWidth="1.5" fill="none" strokeDasharray="1 5" strokeLinecap="round" />
      {running && Array.from({ length: 3 }).map((_, i) => (
        <circle key={i} r="2.6" fill="#8b96a5">
          <animateMotion dur="1.4s" begin={`${i * 0.47}s`} repeatCount="indefinite">
            <mpath href="#fd-in" />
          </animateMotion>
        </circle>
      ))}
      <text x="14" y="96" fontSize="9" fill="#5d6674" fontFamily="ui-monospace,monospace" letterSpacing="0.5">REQUESTS</text>

      {/* central node */}
      <circle cx="176" cy="109" r="34" fill="#161b22" stroke="#f28b82" strokeOpacity="0.6" strokeWidth="1.5" filter="url(#fdglow)" />
      <circle cx="176" cy="109" r="34" fill="none" stroke="#f28b82" strokeOpacity="0.25" strokeWidth="8" />
      <text x="176" y="105" textAnchor="middle" fontSize="9.5" fontWeight="700" fill="#eef2f6" fontFamily="ui-monospace,monospace">CACHE</text>
      <text x="176" y="117" textAnchor="middle" fontSize="9.5" fontWeight="700" fill="#eef2f6" fontFamily="ui-monospace,monospace">MIND</text>

      {branches.map((b) => {
        const pathId = `fd-${b.id}`;
        const d = `M210 109 C 320 109, 340 ${b.y}, 470 ${b.y}`;
        const active = b.rate > 0.01;
        const count = running && active ? Math.max(1, Math.round(b.rate * 4)) : 0;
        const dur = Math.max(0.7, 2.6 - b.rate * 2.0);
        return (
          <g key={b.id}>
            <path
              id={pathId} d={d} fill="none" stroke={b.color}
              strokeOpacity={0.12 + b.rate * 0.55} strokeWidth={1.2 + b.rate * 3.5}
              strokeLinecap="round"
            />
            {Array.from({ length: count }).map((_, i) => (
              <circle key={i} r="3" fill={b.color} filter="url(#fdglow)">
                <animateMotion dur={`${dur}s`} begin={`${(i * dur) / Math.max(count, 1)}s`} repeatCount="indefinite">
                  <mpath href={`#${pathId}`} />
                </animateMotion>
              </circle>
            ))}
            <circle cx="470" cy={b.y} r="16" fill="#12161d" stroke={b.color} strokeOpacity={0.5 + b.rate * 0.5} strokeWidth="1.4" />
            <text x="470" y={b.y - 1} textAnchor="middle" fontSize="9" fontWeight="700" fill={b.color} fontFamily="ui-monospace,monospace">{b.label}</text>
            <text x="470" y={b.y + 9} textAnchor="middle" fontSize="7" fill="#5d6674" fontFamily="ui-monospace,monospace">{b.sub}</text>
            <text x="500" y={b.y + 3} fontSize="9" fill={b.color} fontFamily="ui-monospace,monospace" opacity={active ? 1 : 0.35}>
              {(b.rate * 100).toFixed(0)}%
            </text>
          </g>
        );
      })}
    </svg>
  );
}
