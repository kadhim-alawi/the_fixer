/* Conversion over time, hand-drawn as SVG.
 *
 * No chart library: this is one small plot and the dependency would be larger
 * than the code. It also has to render identically inside a recorded demo, so
 * fewer moving parts is better.
 *
 * The point of this panel is the shape of the story -- one line falling away
 * from the others, a marker where a remediation failed, another where one
 * worked, and the line coming back. Everything here serves that reading.
 */

const SERIES = [
  { key: 'web', label: 'web', color: 'var(--web)' },
  { key: 'ios', label: 'ios', color: 'var(--ios)' },
  { key: 'android', label: 'android', color: 'var(--android)' },
]

export default function Chart({ data }) {
  const W = 396, H = 168, PAD_L = 30, PAD_R = 8, PAD_T = 10, PAD_B = 18
  if (!data || !data.labels || !data.labels.length) {
    return <div className="chart" style={{ height: H, color: 'var(--dimmer)' }}>waiting for data…</div>
  }

  const n = data.labels.length
  const all = SERIES.flatMap((s) => data.series[s.key] || []).filter((v) => v != null)
  const max = Math.max(5, Math.ceil(Math.max(...all, 0) + 1))
  const x = (i) => PAD_L + (i / Math.max(1, n - 1)) * (W - PAD_L - PAD_R)
  const y = (v) => PAD_T + (1 - v / max) * (H - PAD_T - PAD_B)

  // Gaps matter: a bucket with too few sessions is left blank rather than
  // interpolated, so a thin patch of data never looks like a real movement.
  const path = (vals) => {
    let d = '', pen = false
    vals.forEach((v, i) => {
      if (v == null) { pen = false; return }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)} `
      pen = true
    })
    return d.trim()
  }

  const tIndex = (iso) => {
    if (!iso) return null
    const t = new Date(iso).getTime()
    const t0 = new Date(data.labels[0]).getTime()
    const t1 = new Date(data.labels[n - 1]).getTime()
    if (!(t >= t0 && t <= t1) || t1 === t0) return null
    return ((t - t0) / (t1 - t0)) * (n - 1)
  }

  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H}>
        {[0, max / 2, max].map((v, i) => (
          <g key={i}>
            <line x1={PAD_L} x2={W - PAD_R} y1={y(v)} y2={y(v)} stroke="var(--line)" strokeWidth="1" />
            <text x={PAD_L - 6} y={y(v) + 3.5} fill="var(--dimmer)" fontSize="9" textAnchor="end">
              {v.toFixed(0)}%
            </text>
          </g>
        ))}

        {(data.actions || []).map((a, i) => {
          const xi = tIndex(a.at)
          if (xi == null) return null
          const stroke = a.effective === true ? 'var(--good)' : a.effective === false ? 'var(--bad)' : 'var(--dim)'
          return (
            <g key={i}>
              <line x1={x(xi)} x2={x(xi)} y1={PAD_T} y2={H - PAD_B} stroke={stroke} strokeWidth="1" strokeDasharray="3 3" opacity="0.8" />
              <circle cx={x(xi)} cy={PAD_T + 3} r="3" fill={stroke} />
            </g>
          )
        })}

        {SERIES.map((s) => (
          <path key={s.key} d={path(data.series[s.key] || [])} fill="none"
                stroke={s.color} strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" />
        ))}
      </svg>

      <div className="legend">
        {SERIES.map((s) => (
          <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>
        ))}
        <span style={{ marginLeft: 'auto' }}>
          <i style={{ background: 'var(--bad)' }} />ineffective
          <i style={{ background: 'var(--good)', marginLeft: 10 }} />effective
        </span>
      </div>
    </div>
  )
}
