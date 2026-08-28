import { useCallback, useEffect, useRef, useState } from 'react'
import Chart from './Chart.jsx'

const ICON = {
  message: '◆',
  thought: '·',
  tool_call: '›',
  tool_result: '←',
  action: '⚡',
  error: '✕',
}

const STATUS_DOT = {
  SUCCESS: 'good',
  FAILED: 'bad',
  BLOCKED: 'bad',
  REQUIRES_HUMAN: 'bad',
  INSUFFICIENT_EVIDENCE: 'bad',
  TIMEOUT: 'bad',
  SAFETY_LIMIT: 'bad',
}

function clock(iso) {
  return iso ? iso.slice(11, 19) : '--:--:--'
}

export default function App() {
  const [health, setHealth] = useState(null)
  const [incidents, setIncidents] = useState([])
  const [incident, setIncident] = useState('payment_config_regression')
  const [mission, setMission] = useState(null)
  const [events, setEvents] = useState([])
  const [metrics, setMetrics] = useState(null)
  const [starting, setStarting] = useState(false)
  const timelineRef = useRef(null)
  const esRef = useRef(null)

  useEffect(() => {
    fetch('/api/health').then((r) => r.json()).then(setHealth).catch(() => {})
    fetch('/api/incidents').then((r) => r.json()).then((d) => setIncidents(d.incidents)).catch(() => {})
  }, [])

  // Follow the timeline only while the operator is already at the bottom, so
  // scrolling back to read something is not yanked away by the next event.
  useEffect(() => {
    const el = timelineRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120
    if (atBottom) el.scrollTop = el.scrollHeight
  }, [events])

  const refreshMetrics = useCallback((id) => {
    fetch(`/api/missions/${id}/metrics?hours=6`)
      .then((r) => r.json()).then(setMetrics).catch(() => {})
  }, [])

  const start = async () => {
    setStarting(true)
    setEvents([]); setMetrics(null); setMission(null)
    if (esRef.current) esRef.current.close()
    try {
      const res = await fetch('/api/missions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ incident_key: incident, seed: 4242, speed: 180 }),
      })
      const snap = await res.json()
      setMission(snap)
      const es = new EventSource(`/api/missions/${snap.id}/stream`)
      esRef.current = es
      es.onmessage = (m) => {
        const p = JSON.parse(m.data)
        if (p.snapshot) setMission(p.snapshot)
        if (p.type === 'event') setEvents((prev) => [...prev, p.event])
        if (p.type === 'event' && (p.event.kind === 'action' || p.event.kind === 'tool_result')) {
          refreshMetrics(snap.id)
        }
        if (p.type === 'finished') { refreshMetrics(snap.id); es.close() }
      }
      es.onerror = () => es.close()
    } finally {
      setStarting(false)
    }
  }

  const decide = async (approved) => {
    if (!mission?.pending_approval) return
    await fetch(`/api/missions/${mission.id}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approval_id: mission.pending_approval.id, approved }),
    })
  }

  const m = mission?.mission
  const c = m?.counters
  // Has anything the agent did already been verified as effective?
  const fixedAlready = !!m?.actions?.some((a) => a.verified_effective === true)
  const running = mission?.state === 'running'
  const status = m?.status ?? 'IDLE'

  return (
    <div className="app">
      <div className="top">
        <div className="brand">THE <span>FIXER</span></div>
        <div className="meta">{mission ? mission.id.toUpperCase() : 'NO ACTIVE MISSION'}</div>
        <div className="pill">
          <i className={`dot ${running ? 'live' : STATUS_DOT[status] || ''}`} />
          {running ? 'WORKING' : status}
        </div>
        {mission && <div className="meta">SIM {clock(mission.sim_time)}</div>}
        <div className="spacer" />
        <div className="meta">
          {health ? `${health.agent_default.toUpperCase()} · ${health.model || 'no model'}` : ''}
        </div>
        <div className="launcher">
          <select value={incident} onChange={(e) => setIncident(e.target.value)} disabled={running || starting}>
            {incidents.map((i) => <option key={i.key} value={i.key}>{i.label}</option>)}
          </select>
          <button className="primary" onClick={start} disabled={running || starting}>
            {starting ? 'BUILDING…' : running ? 'RUNNING' : 'START MISSION'}
          </button>
        </div>
      </div>

      <div className="objective">
        <span className="label">OBJECTIVE</span>
        <span className="text">
          {mission?.objective ?? 'Our conversion rate has dropped significantly today. Find out why and fix the problem.'}
        </span>
      </div>

      <div className="main">
        <div className="left">
          <div className="timeline" ref={timelineRef}>
            {!events.length && (
              <div className="empty">
                <h2>Give it a problem. Walk away.</h2>
                <p>Start a mission and the agent works on its own.<br />
                   Everything it does appears here as it happens.</p>
              </div>
            )}
            {events.map((e, i) => (
              <div key={i} className={`row ${e.kind}`}>
                <span className="t">{clock(e.sim_time)}</span>
                <span className="icon">{ICON[e.kind] ?? ' '}</span>
                <span className="msg">
                  {e.text}
                  {e.risk && e.risk !== 'LOW' && <span className={`tag ${e.risk}`}>{e.risk}</span>}
                </span>
              </div>
            ))}
          </div>

          {m?.conclusion && (
            <div className={`conclusion ${m.status === 'SUCCESS' ? '' : 'fail'}`}>
              <h3>{m.status === 'SUCCESS' ? '✓ PROBLEM SOLVED' : `✕ ${m.status}`}</h3>
              <dl>
                <dt>root cause</dt><dd>{m.conclusion.root_cause || '—'}</dd>
                <dt>evidence</dt><dd>{m.conclusion.evidence_summary || '—'}</dd>
                <dt>measured</dt><dd>{m.conclusion.before_after || '—'}</dd>
              </dl>
            </div>
          )}
        </div>

        <div className="right">
          <div className="panel">
            <h2>CONVERSION RATE <span>last 6h</span></h2>
            <Chart data={metrics} />
          </div>

          <div className="panel">
            <h2>HYPOTHESES <span>{m?.hypotheses?.length || 0}</span></h2>
            <div className="body">
              {!m?.hypotheses?.length && <span style={{ color: 'var(--dimmer)' }}>none yet</span>}
              {m?.hypotheses?.map((h) => (
                <div key={h.id} className={`hyp ${h.state}`}>
                  <div className="head">
                    <span className="id">{h.id}</span>
                    <span className={`state ${h.state}`}>{h.state}</span>
                    <span className="conf">{(h.confidence * 100).toFixed(0)}%</span>
                  </div>
                  <div className="bar"><i style={{ width: `${h.confidence * 100}%` }} /></div>
                  <div className="stmt">{h.statement}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="panel">
            <h2>EVIDENCE</h2>
            <div className="counters">
              <div><div className="n">{c?.findings ?? 0}</div><div className="k">FINDINGS</div></div>
              <div><div className="n">{c?.actions_taken ?? 0}</div><div className="k">ACTIONS</div></div>
              <div><div className="n">{c?.verifications ?? 0}</div><div className="k">VERIFIED</div></div>
              <div>
                <div className={`n ${c?.failed_remediations ? 'bad' : ''}`}>{c?.failed_remediations ?? 0}</div>
                <div className="k">FAILED FIX</div>
              </div>
              <div>
                <div className={`n ${c?.recovered_after_failure ? 'good' : ''}`}>
                  {c?.recovered_after_failure ? 'YES' : '—'}
                </div>
                <div className="k">RECOVERED</div>
              </div>
              <div><div className="n">{m?.hypotheses?.length ?? 0}</div><div className="k">HYPOTHESES</div></div>
            </div>
          </div>

          <div className="panel">
            <h2>REMEDIATIONS <span>{m?.actions?.length || 0}</span></h2>
            <div className="body">
              {!m?.actions?.length && <span style={{ color: 'var(--dimmer)' }}>none yet</span>}
              {m?.actions?.map((a) => (
                <div className="act" key={a.seq}>
                  <span className="seq">{a.seq}</span>
                  <span className="name">{a.tool}</span>
                  <span className={`verdict ${a.verified_effective === true ? 'eff' : a.verified_effective === false ? 'ineff' : 'pending'}`}>
                    {a.verified_effective === true ? 'EFFECTIVE'
                      : a.verified_effective === false ? 'INEFFECTIVE' : 'UNVERIFIED'}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {mission?.pending_approval && (
        <div className="scrim">
          <div className="modal">
            <header><h3>⚠ APPROVAL REQUIRED</h3></header>
            <div className="body">
              {/* Without this line the dialog reads as the agent going rogue.
                  It is almost always a follow-up after the objective is already
                  met, and a reader has seconds to work that out. */}
              <div className={`ctx ${fixedAlready ? 'ok' : ''}`}>
                {fixedAlready
                  ? 'Objective already met — a remediation has been applied and verified. This is a follow-up action.'
                  : 'The mission is still working towards the objective.'}
              </div>
              <dl>
                <dt>action</dt><dd>{mission.pending_approval.tool}</dd>
                <dt>parameters</dt><dd>{JSON.stringify(mission.pending_approval.args)}</dd>
                <dt>risk</dt><dd>{mission.pending_approval.risk}</dd>
                <dt>reversible</dt><dd>{mission.pending_approval.reversibility}</dd>
                <dt>agent's reason</dt><dd>{mission.pending_approval.reason || '—'}</dd>
              </dl>
              <div style={{ color: 'var(--dim)', fontSize: 12 }}>
                The agent cannot take this action on its own. It has been told so and
                will continue without it unless you approve.
              </div>
            </div>
            <footer>
              <button className="primary" onClick={() => decide(true)}>APPROVE</button>
              <button className="danger" onClick={() => decide(false)}>REJECT</button>
            </footer>
          </div>
        </div>
      )}
    </div>
  )
}
