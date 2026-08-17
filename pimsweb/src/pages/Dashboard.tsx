import { useApp } from '../App'
import { api } from '../lib/api'
import type { Balance, PendingShipment, QcRecord } from '../lib/types'
import {
  Alert, Badge, Card, DataTable, ErrorBox, Loading, Meter, Stat,
  fmtDateTime, fmtLbs, useAsync,
} from '../components/ui'

interface DashboardData {
  open_orders: number
  overdue_orders: number
  awaiting_shipment: number
  open_by_type: Record<string, number>
  transactions_24h: number
  out_of_spec_recent: number
  lims: {
    status: string
    detail: string
    age_hours: number | null
    sources: string[]
    expected_source: string
    samples: number
  }
}

export default function Dashboard() {
  const { plantId, plantCode, navigate } = useApp()

  const summary = useAsync(
    () => api.get<DashboardData>(`/api/dashboard?plant_id=${plantId}`), [plantId],
  )
  const shipments = useAsync(
    () => api.get<PendingShipment[]>(`/api/shipments/pending?plant_id=${plantId}`), [plantId],
  )
  const balances = useAsync(
    () => api.get<Balance[]>(`/api/balances?plant_id=${plantId}`), [plantId],
  )
  const outOfSpec = useAsync(
    () => api.get<QcRecord[]>(`/api/qc/out-of-spec?plant_id=${plantId}&days=14&limit=10`), [plantId],
  )

  if (summary.loading) return <Loading />
  if (summary.error) return <ErrorBox error={summary.error} />
  const data = summary.data!

  const tanks = (balances.data ?? [])
    .filter((row) => row.location_type === 'Tank')
    .sort((a, b) => (b.percent_full ?? 0) - (a.percent_full ?? 0))

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{plantCode} plant overview</h1>
          <div className="sub">Open work, inventory and quality at a glance.</div>
        </div>
        <div className="actions">
          <button onClick={() => navigate('operations')}>Plant floor</button>
          <button className="primary" onClick={() => navigate('orders')}>Orders</button>
        </div>
      </div>

      {data.lims.status !== 'ok' && (
        <div style={{ marginBottom: 16 }}>
          <Alert tone={data.lims.status === 'failed' ? 'danger' : 'warn'} title="LIMS feed needs attention">
            {data.lims.detail} Lab results shown against samples come from{' '}
            <span className="mono">{data.lims.sources.join(', ') || 'no source'}</span>.
          </Alert>
        </div>
      )}

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <Stat
          label="Open orders"
          value={data.open_orders}
          foot={Object.entries(data.open_by_type).map(([code, count]) => `${code} ${count}`).join(' · ') || 'none'}
        />
        <Stat
          label="Overdue"
          value={data.overdue_orders}
          tone={data.overdue_orders ? 'warn' : undefined}
          foot="Past due date, not closed"
        />
        <Stat
          label="Trailers awaiting ship"
          value={data.awaiting_shipment}
          foot="Loaded, BOL not completed"
        />
        <Stat
          label="Out of spec (14 days)"
          value={data.out_of_spec_recent}
          tone={data.out_of_spec_recent ? 'alert' : undefined}
          foot="QC results outside product limits"
        />
      </div>

      <div className="grid cols-2">
        <Card
          title="Trailers waiting to ship"
          subtitle={`${shipments.data?.length ?? 0} staged load(s)`}
          actions={<button className="sm" onClick={() => navigate('operations/ship')}>Ship trailer</button>}
          tight
        >
          {shipments.loading ? <Loading /> : (
            <DataTable
              rows={shipments.data ?? []}
              rowKey={(row) => row.stage_id}
              empty="Nothing staged — every loaded trailer has shipped."
              onRowClick={(row) => navigate(`orders/${row.order_id}`)}
              columns={[
                { key: 'order_id', label: 'Order' },
                { key: 'customer_name', label: 'Customer' },
                { key: 'material_number', label: 'Material' },
                { key: 'trailer_number', label: 'Trailer' },
                { key: 'quantity', label: 'Lbs', numeric: true, render: (row) => fmtLbs(row.quantity) },
                { key: 'loaded_at', label: 'Loaded', render: (row) => fmtDateTime(row.loaded_at) },
              ]}
            />
          )}
        </Card>

        <Card
          title="Tank levels"
          subtitle="Current balance against stated capacity"
          actions={<button className="sm" onClick={() => navigate('inventory')}>All locations</button>}
          tight
        >
          {balances.loading ? <Loading /> : (
            <DataTable
              rows={tanks.slice(0, 12)}
              rowKey={(row) => `${row.location_id}-${row.material_id}`}
              empty="No inventory recorded at this plant."
              columns={[
                { key: 'location_number', label: 'Tank' },
                { key: 'material_number', label: 'Material' },
                { key: 'balance', label: 'Lbs', numeric: true, render: (row) => fmtLbs(row.balance) },
                { key: 'pct', label: 'Full', render: (row) => <Meter value={row.balance} max={row.max_capacity} /> },
              ]}
            />
          )}
        </Card>
      </div>

      <Card
        title="Recent out-of-spec results"
        subtitle="QC readings outside the product's published limits, last 14 days"
        tight
      >
        {outOfSpec.loading ? <Loading /> : (
          <DataTable
            rows={outOfSpec.data ?? []}
            rowKey={(row) => row.qc_id}
            empty="No out-of-spec results in the last 14 days."
            onRowClick={(row) => navigate(`orders/${row.order_id}`)}
            columns={[
              { key: 'test_date', label: 'Test date' },
              { key: 'order_id', label: 'Order' },
              { key: 'material_number', label: 'Material', render: (row) => `${row.material_number} ${row.material_description ?? ''}` },
              { key: 'sample_number', label: 'Sample #' },
              {
                key: 'flags',
                label: 'Outside limits',
                render: (row) => (
                  <div className="row" style={{ gap: 6 }}>
                    {row.evaluations.map((evaluation) => (
                      <Badge key={evaluation.analyte} tone="danger">
                        {evaluation.label} {evaluation.value}
                        {evaluation.max_value !== null ? ` > ${evaluation.max_value}` : ''}
                        {evaluation.min_value !== null && evaluation.value !== null && evaluation.value < evaluation.min_value ? ` < ${evaluation.min_value}` : ''}
                      </Badge>
                    ))}
                  </div>
                ),
              },
            ]}
          />
        )}
      </Card>
    </>
  )
}
