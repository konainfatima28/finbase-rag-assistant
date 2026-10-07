"use client";

import { useState } from "react";

// Single-series horizontal bars (one hue, no legend needed): thin marks, 4px rounded data-end, recessive
// track, value label in text ink (not the series color), hover/focus tooltip, and a screen-reader table.
export function BarList({ rows, max, compact = false }: { rows: { label: string; value: number | null }[]; max: number; compact?: boolean }) {
  const [hover, setHover] = useState<number | null>(null);
  return (
    <div>
      <ul className={compact ? "space-y-1.5" : "space-y-2"} aria-hidden="true">
        {rows.map((row, i) => {
          const pct = row.value === null ? 0 : Math.max(0, Math.min(1, row.value / max)) * 100;
          return (
            <li key={row.label} className="relative grid grid-cols-[minmax(7rem,40%)_1fr_3.2rem] items-center gap-2" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <span className="truncate text-xs text-ink-2" title={row.label}>
                {row.label}
              </span>
              <span className="relative h-3 rounded-r-[4px] bg-surface-2">
                {row.value !== null && <span className="absolute inset-y-0 left-0 rounded-r-[4px] bg-chart-1" style={{ width: `${pct}%` }} />}
                {hover === i && (
                  <span className="absolute -top-7 left-1/2 z-10 -translate-x-1/2 whitespace-nowrap rounded-md bg-ink px-2 py-0.5 text-[11px] text-bg shadow">
                    {row.label}: {row.value === null ? "not measured" : row.value.toFixed(3)}
                  </span>
                )}
              </span>
              <span className="text-right text-xs tabular-nums text-ink">{row.value === null ? "—" : row.value.toFixed(3)}</span>
            </li>
          );
        })}
      </ul>
      <table className="sr-only">
        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <th scope="row">{row.label}</th>
              <td>{row.value === null ? "not measured" : row.value.toFixed(3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
