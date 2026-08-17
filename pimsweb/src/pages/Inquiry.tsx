/* The Inquiry window: Location Balance, Activity, Order and QC, with CSV
 * export. Same four tabs the legacy client had, same filters, plus the
 * out-of-spec toggle QC asked for. */

import { useState } from 'react'
import { useApp, useToast } from '../App'
import { api } from '../lib/api'
import {
  Badge, Card, DataTable, ErrorBox, Field, Loading, Tabs, daysFromToday, useAsync,
} from '../components/ui'

const TABS = [
  { key: 'balance', label: 'Location balance' },
  { key: 'activity', label: 'Activity' },
  { key: 'order', label: 'Order' },
  { key: 'qc', label: 'QC' },
]

interface InquiryResult {
  tab: string
  columns: { name: string; label: string }[]
  rows: Record<string, any>[]
  row_count: number
}

export default function Inquiry({ initialTab }: { initialTab?: string }) {
  const { plantId, reference } = useApp()
  const toast = useToast()
  const [tab, setTab] = useState(TABS.some((t) => t.key === initialTab) ? initialTab! : 'balance')
  const [filters, setFilters] = useState<Record<string, any>>({
    date_from: daysFromToday(-30),
    date_to: daysFromToday(1),
  })
  const [runToken, setRunToken] = useState(0)

  const payload = { plant_id: plantId, ...clean(filters) }
  const result = useAsync(
    () => api.post<InquiryResult>(`/api/inquiry/${tab}`, payload), [tab, plantId, runToken],
  )

  const set = (patch: Record<string, any>) => setFilters((current) => ({ ...current, ...patch }))

  async function exportCsv() {
    try {
      await api.download(`/api/inquiry/${tab}/csv`, payload, `pims-${tab}.csv`)
    } catch (error) {
      toast.push('error', 'Export failed', (error as Error).message)
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Inquiry</h1>
          <div className="sub">Read-only views across orders, movements, balances and QC.</div>
        </div>
        <div className="actions">
          <button onClick={exportCsv} disabled={!result.data?.row_count}>Export to CSV</button>
          <button className="primary" onClick={() => setRunToken((n) => n + 1)}>Execute</button>
        </div>
      </div>

      <Tabs tabs={TABS} active={tab} onChange={(key) => { setTab(key); setRunToken((n) => n + 1) }} />

      <Card title="Filters">
        <div className="form-grid">
          <Field label="Location">
            <select value={filters.location_id ?? ''} onChange={(e) => set({ location_id: e.target.value })}>
              <option value="">All locations</option>
              {reference.locations.filter((l) => l.plant_id === plantId).map((location) => (
                <option key={location.location_id} value={location.location_id}>
                  {location.number} — {location.description}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Material">
            <select value={filters.material_id ?? ''} onChange={(e) => set({ material_id: e.target.value })}>
              <option value="">All materials</option>
              {reference.materials.map((material) => (
                <option key={material.material_id} value={material.material_id}>
                  {material.number} — {material.description}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Order id">
            <input value={filters.order_id ?? ''} onChange={(e) => set({ order_id: e.target.value })} />
          </Field>

          {tab === 'balance' && (
            <Field label="Point in time" hint="Balance as it stood at that moment.">
              <input
                type="datetime-local"
                value={filters.as_of ?? ''}
                onChange={(e) => set({ as_of: e.target.value })}
              />
            </Field>
          )}

          {tab !== 'balance' && (
            <>
              <Field label="From date">
                <input type="date" value={filters.date_from ?? ''} onChange={(e) => set({ date_from: e.target.value })} />
              </Field>
              <Field label="To date">
                <input type="date" value={filters.date_to ?? ''} onChange={(e) => set({ date_to: e.target.value })} />
              </Field>
            </>
          )}

          {tab === 'activity' && (
            <Field label="Operation">
              <select value={filters.operation ?? ''} onChange={(e) => set({ operation: e.target.value })}>
                <option value="">All operations</option>
                {reference.transaction_types.map((type) => (
                  <option key={type.transaction_type_id} value={type.code}>{type.code}</option>
                ))}
              </select>
            </Field>
          )}

          {tab === 'qc' && (
            <>
              <Field label="Sample #">
                <input value={filters.sample_number ?? ''} onChange={(e) => set({ sample_number: e.target.value })} />
              </Field>
              <div className="field" style={{ justifyContent: 'flex-end' }}>
                <label className="row small" style={{ gap: 6 }}>
                  <input
                    type="checkbox"
                    checked={Boolean(filters.out_of_spec_only)}
                    onChange={(e) => set({ out_of_spec_only: e.target.checked })}
                  />
                  Out-of-spec results only
                </label>
              </div>
            </>
          )}

          {tab === 'activity' && (
            <div className="field" style={{ justifyContent: 'flex-end' }}>
              <label className="row small" style={{ gap: 6 }}>
                <input
                  type="checkbox"
                  checked={Boolean(filters.include_voided)}
                  onChange={(e) => set({ include_voided: e.target.checked })}
                />
                Include voided and reversing entries
              </label>
            </div>
          )}
        </div>
      </Card>

      <Card
        title="Results"
        subtitle={result.data ? `${result.data.row_count.toLocaleString()} record(s) returned` : undefined}
        tight
      >
        {result.loading ? <Loading /> : result.error ? <ErrorBox error={result.error} /> : (
          <DataTable
            rows={result.data?.rows ?? []}
            rowKey={(_row, index) => index}
            maxHeight="60vh"
            empty="No records for these filters."
            columns={(result.data?.columns ?? []).map((column) => ({
              key: column.name,
              label: column.label,
              numeric: /qty|balance|lbs|moisture|temp|ffa|tfa|ph|percent|capacity|spintest/i.test(column.name),
              render: column.name === 'spec_status'
                ? (row: any) => (
                    <Badge tone={row.spec_status === 'out_of_spec' ? 'danger' : row.spec_status === 'incomplete' ? 'warn' : 'ok'}>
                      {String(row.spec_status).replace('_', ' ')}
                    </Badge>
                  )
                : undefined,
            }))}
          />
        )}
      </Card>
    </>
  )
}

function clean(filters: Record<string, any>): Record<string, any> {
  const out: Record<string, any> = {}
  for (const [key, value] of Object.entries(filters)) {
    if (value === '' || value === undefined || value === null || value === false) continue
    out[key] = value
  }
  return out
}
