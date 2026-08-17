/* Support console.
 *
 * The half of this project that is not a face lift. Everything a support
 * engineer previously had to reconstruct by hand — is the LIMS feed current,
 * who changed this order, what is failing, which data is inconsistent — is on
 * one screen, readable by anyone with the supervisor role. */

import { useState } from 'react'
import { useToast } from '../App'
import { api } from '../lib/api'
import type { AuditEntry, DataQuality, Diagnostics } from '../lib/types'
import {
  Alert, Badge, Card, DataTable, ErrorBox, Loading, Tabs, fmtDateTime, useAsync,
} from '../components/ui'

export default function Support() {
  const [tab, setTab] = useState('health')
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Support console</h1>
          <div className="sub">System health, data quality, audit trail and recent failures.</div>
        </div>
      </div>
      <Tabs
        active={tab}
        onChange={setTab}
        tabs={[
          { key: 'health', label: 'Health' },
          { key: 'quality', label: 'Data quality' },
          { key: 'alerts', label: 'Alerts' },
          { key: 'jobs', label: 'Scheduled jobs' },
          { key: 'audit', label: 'Audit trail' },
          { key: 'errors', label: 'Errors' },
        ]}
      />
      {tab === 'health' && <Health />}
      {tab === 'quality' && <Quality />}
      {tab === 'alerts' && <Alerts />}
      {tab === 'jobs' && <Jobs />}
      {tab === 'audit' && <Audit />}
      {tab === 'errors' && <Errors />}
    </>
  )
}

function statusTone(status: string): 'ok' | 'warn' | 'danger' {
  return status === 'ok' ? 'ok' : status === 'failed' ? 'danger' : 'warn'
}

function Health() {
  const diagnostics = useAsync(() => api.get<Diagnostics>('/api/support/diagnostics'), [])
  if (diagnostics.loading) return <Loading />
  if (diagnostics.error) return <ErrorBox error={diagnostics.error} />
  const data = diagnostics.data!

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <Alert
          tone={statusTone(data.status)}
          title={`Overall status: ${data.status}`}
        >
          PIMS {data.version} · environment {data.environment} · checked {fmtDateTime(data.checked_at)}
          <button className="ghost sm" style={{ marginLeft: 10 }} onClick={diagnostics.reload}>Re-run checks</button>
        </Alert>
      </div>

      <div className="grid cols-2">
        {Object.entries(data.checks).map(([name, check]: [string, any]) => (
          <Card
            key={name}
            title={<span className="row" style={{ gap: 8 }}>
              {name.replace('_', ' ')}
              <Badge tone={statusTone(check.status)}>{check.status}</Badge>
            </span>}
          >
            <div className="stack">
              <div>{check.detail}</div>
              <CheckDetail name={name} check={check} />
            </div>
          </Card>
        ))}
      </div>

      <Card title="Configuration" subtitle="What this process is actually running with">
        <dl className="kv">
          {Object.entries(data.configuration).map(([key, value]) => (
            <div key={key} style={{ display: 'contents' }}>
              <dt>{key}</dt>
              <dd className="mono">{String(value)}</dd>
            </div>
          ))}
          <dt>started</dt><dd className="mono">{fmtDateTime(data.runtime.started_at)}</dd>
        </dl>
      </Card>
    </>
  )
}

function CheckDetail({ name, check }: { name: string; check: any }) {
  if (name === 'lims') {
    return (
      <dl className="kv small">
        <dt>Source(s)</dt><dd className="mono">{(check.sources ?? []).join(', ') || '—'}</dd>
        <dt>Expected</dt><dd className="mono">{check.expected_source}</dd>
        <dt>Last refresh</dt><dd>{fmtDateTime(check.last_retrieved)}</dd>
        <dt>Age</dt><dd>{check.age_hours === null ? '—' : `${check.age_hours} h`} (warn {check.warn_after_hours} h / fail {check.fail_after_hours} h)</dd>
        <dt>Samples</dt><dd className="num">{check.samples?.toLocaleString?.() ?? '—'}</dd>
        <dt>Test codes</dt><dd className="num">{check.test_codes ?? '—'}</dd>
      </dl>
    )
  }
  if (name === 'database') {
    return (
      <dl className="kv small">
        <dt>Path</dt><dd className="mono">{check.path}</dd>
        <dt>Size</dt><dd>{check.size_mb} MB across {check.tables} tables</dd>
        {Object.entries(check.counts ?? {}).map(([table, count]) => (
          <div key={table} style={{ display: 'contents' }}>
            <dt>{table}</dt><dd className="num">{Number(count).toLocaleString()}</dd>
          </div>
        ))}
      </dl>
    )
  }
  if (name === 'product_setup') {
    const missing = [...(check.materials_without_specs ?? []), ...(check.materials_without_tests ?? [])]
    if (!missing.length && !check.needs_review) return null
    return (
      <div className="small">
        {check.needs_review ? <div>{check.needs_review} limit(s) awaiting confirmation — see Products &amp; limits.</div> : null}
        {missing.length ? (
          <div style={{ marginTop: 6 }}>
            Products needing setup: {missing.slice(0, 8).map((m: any) => m.number).join(', ')}
            {missing.length > 8 ? ` and ${missing.length - 8} more` : ''}
          </div>
        ) : null}
      </div>
    )
  }
  if (name === 'errors' && check.recent?.length) {
    return (
      <div className="small">
        Most recent: <span className="mono">{check.recent[0].code}</span> on{' '}
        <span className="mono">{check.recent[0].path}</span>
      </div>
    )
  }
  return null
}

function Quality() {
  const quality = useAsync(() => api.get<DataQuality>('/api/support/data-quality'), [])
  if (quality.loading) return <Loading />
  if (quality.error) return <ErrorBox error={quality.error} />
  const data = quality.data!

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <Alert tone={data.total_findings ? 'warn' : 'ok'} title={`${data.total_findings} finding(s)`}>
          Generated {fmtDateTime(data.generated_at)}. Each finding lists the rows behind it and what to do about it.
        </Alert>
      </div>
      <div className="stack">
        {data.findings.map((finding) => (
          <Card
            key={finding.key}
            title={<span className="row" style={{ gap: 8 }}>
              {finding.label}
              <Badge tone={finding.count === 0 ? 'ok' : finding.severity === 'high' ? 'danger' : 'warn'}>
                {finding.count}
              </Badge>
            </span>}
            subtitle={finding.action}
            tight
          >
            {finding.count === 0 ? (
              <div className="empty">Nothing to action.</div>
            ) : (
              <DataTable
                rows={finding.rows}
                rowKey={(_row, index) => index}
                maxHeight="280px"
                columns={Object.keys(finding.rows[0] ?? {}).map((key) => ({
                  key,
                  label: key.replace(/_/g, ' '),
                  numeric: /qty|quantity|balance|capacity|id$/.test(key),
                }))}
              />
            )}
          </Card>
        ))}
      </div>
    </>
  )
}

function Alerts() {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const alerts = useAsync(
    () => api.get<{ alerts: any[]; settings: any }>('/api/alerts?limit=100'), [],
  )

  async function evaluateNow(send: boolean) {
    setBusy(true)
    try {
      const result = await api.post<any>('/api/alerts/run', { send })
      toast.push(
        'info',
        send ? `${result.new.length} alert(s) raised` : `${result.evaluated} condition(s) open`,
        send
          ? `${result.delivered} delivered, ${result.suppressed} already reported`
          : 'Dry run — nothing recorded or sent.',
      )
      alerts.reload()
    } catch (error) {
      toast.push('error', 'Could not evaluate the rules', (error as Error).message)
    } finally { setBusy(false) }
  }

  async function acknowledge(alertId: number) {
    await api.post(`/api/alerts/${alertId}/acknowledge`, {})
    alerts.reload()
  }

  if (alerts.loading) return <Loading />
  if (alerts.error) return <ErrorBox error={alerts.error} />
  const settings = alerts.data!.settings

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <Alert
          tone={settings.webhook_configured ? 'ok' : 'warn'}
          title={settings.webhook_configured
            ? `Delivering ${settings.min_severity} and above to the configured webhook`
            : 'No webhook configured — alerts are recorded but not delivered'}
        >
          The same condition is not re-sent inside {settings.repeat_hours} h. Set
          <span className="mono"> alerts.webhook_url</span> to a Teams or Slack incoming webhook to
          deliver them.
          <button className="ghost sm" style={{ marginLeft: 8 }} disabled={busy} onClick={() => evaluateNow(false)}>
            Evaluate now (dry run)
          </button>
          <button className="ghost sm" disabled={busy} onClick={() => evaluateNow(true)}>
            Evaluate and send
          </button>
        </Alert>
      </div>

      <Card title="Recent alerts" subtitle="Newest first" tight>
        <DataTable
          rows={alerts.data!.alerts}
          rowKey={(row) => row.alert_id}
          maxHeight="60vh"
          empty="Nothing raised yet."
          columns={[
            { key: 'created_at', label: 'When', render: (row) => fmtDateTime(row.created_at) },
            {
              key: 'severity',
              label: 'Severity',
              render: (row) => (
                <Badge tone={row.severity === 'critical' ? 'danger' : row.severity === 'warning' ? 'warn' : undefined}>
                  {row.severity}
                </Badge>
              ),
            },
            { key: 'rule', label: 'Rule' },
            { key: 'subject', label: 'Subject' },
            {
              key: 'delivered',
              label: 'Delivered',
              render: (row) => row.delivered
                ? <Badge tone="ok">sent</Badge>
                : row.error
                  ? <Badge tone="danger">{row.error.slice(0, 40)}</Badge>
                  : <span className="muted small">recorded only</span>,
            },
            {
              key: 'ack',
              label: '',
              render: (row) => row.acknowledged_at
                ? <span className="small muted">ack {row.acknowledged_by}</span>
                : <button className="sm" onClick={() => acknowledge(row.alert_id)}>Acknowledge</button>,
            },
          ]}
        />
      </Card>
    </>
  )
}

function Jobs() {
  const toast = useToast()
  const [busy, setBusy] = useState<string | null>(null)
  const jobs = useAsync(() => api.get<{ jobs: Record<string, any>; recent: any[] }>('/api/jobs'), [])

  const RUNNABLE = [
    { key: 'daily', label: 'Daily run', blurb: 'standing orders, auto-close, alerts, digest' },
    { key: 'alerts', label: 'Alerts', blurb: 'evaluate the rules' },
    { key: 'auto-close', label: 'Auto-close', blurb: 'close fulfilled, shipped, QC-d orders' },
    { key: 'recurring', label: 'Standing orders', blurb: 'create the orders due today' },
    { key: 'lims-sync', label: 'LIMS sync', blurb: 'pull lab results into the projection' },
    { key: 'gp-sync', label: 'GP sync', blurb: 'pull customers, vendors and order headers' },
  ]

  async function run(job: string, dryRun: boolean) {
    setBusy(job)
    try {
      const result = await api.post<any>(`/api/jobs/${job}/run`, { dry_run: dryRun })
      toast.push('success', `${job} ${dryRun ? 'dry run' : 'ran'}`, summarise(result))
      jobs.reload()
    } catch (error) {
      toast.push('error', `${job} failed`, (error as Error).message)
    } finally { setBusy(null) }
  }

  if (jobs.loading) return <Loading />
  if (jobs.error) return <ErrorBox error={jobs.error} />

  return (
    <>
      <Card title="Run a job now" subtitle="The same code cron calls — dry run first if you are unsure">
        <div className="grid cols-3">
          {RUNNABLE.map((job) => {
            const status = jobs.data!.jobs[job.key.replace('-', '_')]
            return (
              <div key={job.key} className="card" style={{ boxShadow: 'none' }}>
                <div className="body">
                  <div className="row" style={{ justifyContent: 'space-between' }}>
                    <strong>{job.label}</strong>
                    {status && (
                      <Badge tone={status.last_status === 'ok' ? 'ok' : 'danger'}>
                        {status.hours_ago === null ? status.last_status : `${status.hours_ago} h ago`}
                      </Badge>
                    )}
                  </div>
                  <div className="small muted" style={{ margin: '4px 0 10px' }}>{job.blurb}</div>
                  <div className="row">
                    <button className="sm" disabled={busy === job.key} onClick={() => run(job.key, true)}>
                      Dry run
                    </button>
                    <button className="sm primary" disabled={busy === job.key} onClick={() => run(job.key, false)}>
                      Run
                    </button>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </Card>

      <Card title="Recent runs" tight>
        <DataTable
          rows={jobs.data!.recent}
          rowKey={(row) => row.run_id}
          empty="No scheduled work has run yet."
          columns={[
            { key: 'job', label: 'Job' },
            { key: 'started_at', label: 'Started', render: (row) => fmtDateTime(row.started_at) },
            {
              key: 'status',
              label: 'Status',
              render: (row) => (
                <Badge tone={row.status === 'ok' ? 'ok' : row.status === 'failed' ? 'danger' : 'warn'}>
                  {row.status}
                </Badge>
              ),
            },
            { key: 'detail', label: 'Detail', render: (row) => <span className="small mono">{row.detail}</span> },
            { key: 'error', label: 'Error' },
          ]}
        />
      </Card>
    </>
  )
}

function summarise(result: any): string {
  if (result?.created) return `${result.created.length} order(s) created`
  if (result?.closed) return `${result.closed.length} order(s) closed`
  if (result?.would_close) return `${result.would_close.length} order(s) would close`
  if (result?.written !== undefined) return `${result.written} row(s) written`
  if (result?.counts) return JSON.stringify(result.counts)
  if (result?.new) return `${result.new.length} new, ${result.suppressed} suppressed`
  return 'done'
}

function Audit() {
  const [username, setUsername] = useState('')
  const trail = useAsync(
    () => api.get<AuditEntry[]>(`/api/support/audit?limit=200${username ? `&username=${encodeURIComponent(username)}` : ''}`),
    [username],
  )

  return (
    <Card
      title="Audit trail"
      subtitle="Every create, update, void and sign-in"
      actions={
        <input
          placeholder="Filter by username"
          style={{ width: 200 }}
          value={username}
          onChange={(event) => setUsername(event.target.value)}
        />
      }
      tight
    >
      {trail.loading ? <Loading /> : trail.error ? <ErrorBox error={trail.error} /> : (
        <DataTable
          rows={trail.data ?? []}
          rowKey={(row) => row.audit_id}
          maxHeight="65vh"
          empty="Nothing recorded yet."
          columns={[
            { key: 'occurred_at', label: 'When', render: (row) => fmtDateTime(row.occurred_at) },
            { key: 'username', label: 'User' },
            { key: 'action', label: 'Action', render: (row) => <Badge>{row.action}</Badge> },
            { key: 'entity', label: 'Entity', render: (row) => `${row.entity} ${row.entity_id}` },
            { key: 'summary', label: 'Summary' },
            {
              key: 'flags',
              label: '',
              render: (row) => row.detail?.acknowledged_warnings
                ? <Badge tone="warn">saved over warnings</Badge>
                : null,
            },
          ]}
        />
      )}
    </Card>
  )
}

function Errors() {
  const errors = useAsync(
    () => api.get<{ errors: any[]; slow_requests: any[]; counters: Record<string, number> }>(
      '/api/support/errors?limit=100',
    ), [],
  )
  if (errors.loading) return <Loading />
  if (errors.error) return <ErrorBox error={errors.error} />
  const data = errors.data!

  return (
    <>
      <Card title="Counters" subtitle="Since this process started">
        <div className="row" style={{ gap: 10 }}>
          {Object.entries(data.counters).length === 0 && <span className="muted">No requests recorded yet.</span>}
          {Object.entries(data.counters).map(([key, value]) => (
            <Badge key={key} tone={key.startsWith('error') || key === 'server_errors' ? 'danger' : undefined}>
              {key}: {value}
            </Badge>
          ))}
        </div>
      </Card>

      <Card title="Recent failures" subtitle="Correlation ids match the X-Correlation-Id header and the log line" tight>
        <DataTable
          rows={data.errors}
          rowKey={(_row, index) => index}
          empty="No failures recorded."
          columns={[
            { key: 'at', label: 'When', render: (row) => fmtDateTime(row.at) },
            { key: 'code', label: 'Code', render: (row) => <Badge tone="danger">{row.code}</Badge> },
            { key: 'path', label: 'Endpoint', render: (row) => `${row.method} ${row.path}` },
            { key: 'message', label: 'Message' },
            { key: 'correlation_id', label: 'Correlation id', render: (row) => <span className="mono">{row.correlation_id}</span> },
          ]}
        />
      </Card>

      <Card title="Slow requests" subtitle="Anything over 750 ms" tight>
        <DataTable
          rows={data.slow_requests}
          rowKey={(_row, index) => index}
          empty="Nothing slow recorded."
          columns={[
            { key: 'at', label: 'When', render: (row) => fmtDateTime(row.at) },
            { key: 'path', label: 'Endpoint', render: (row) => `${row.method} ${row.path}` },
            { key: 'duration_ms', label: 'ms', numeric: true },
            { key: 'correlation_id', label: 'Correlation id', render: (row) => <span className="mono">{row.correlation_id}</span> },
          ]}
        />
      </Card>
    </>
  )
}
