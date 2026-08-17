/* Products & limits.
 *
 * In the legacy system this lived in spreadsheets and in LIMS limit screens,
 * which is why "out-of-spec results aren't flagged" was a documented
 * troubleshooting entry: nobody could see whether a product had limits set at
 * all. Here the limits are data in PIMS, editable, and the review queue shows
 * the rows whose source disagreed with itself. */

import { useMemo, useState } from 'react'
import { useApp, useToast } from '../App'
import { api } from '../lib/api'
import {
  Alert, Badge, Card, DataTable, ErrorBox, Field, Loading, Modal, Tabs, useAsync,
} from '../components/ui'

interface SpecRow {
  spec_id: number
  material_id: number
  number: string
  description: string
  family: string
  analyte: string
  min_value: number | null
  max_value: number | null
  source: string
  note: string
  needs_review: number
}

export default function Specs() {
  const { reference, can } = useApp()
  const toast = useToast()
  const [tab, setTab] = useState('all')
  const [family, setFamily] = useState('')
  const [editing, setEditing] = useState<SpecRow | null>(null)

  const specs = useAsync(
    () => api.get<SpecRow[]>(`/api/specs${family ? `?family=${encodeURIComponent(family)}` : ''}`),
    [family],
  )

  const families = useMemo(
    () => [...new Set(reference.materials.map((m) => m.family).filter(Boolean))].sort(),
    [reference.materials],
  )

  const rows = (specs.data ?? []).filter((row) => (tab === 'review' ? row.needs_review : true))
  const reviewCount = (specs.data ?? []).filter((row) => row.needs_review).length

  async function save(row: SpecRow, min: string, max: string, note: string, resolved: boolean) {
    try {
      await api.put('/api/specs', {
        material_id: row.material_id,
        analyte: row.analyte,
        min_value: min === '' ? null : Number(min),
        max_value: max === '' ? null : Number(max),
        note,
        source: 'PIMS',
        needs_review: !resolved && Boolean(row.needs_review),
      })
      toast.push('success', `Limit updated for ${row.number} ${row.analyte}`)
      setEditing(null)
      specs.reload()
    } catch (error) {
      toast.push('error', 'Could not save the limit', (error as Error).message)
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Products & limits</h1>
          <div className="sub">
            What each product is tested for, and the min/max used to flag results.
          </div>
        </div>
      </div>

      {reviewCount > 0 && (
        <div style={{ marginBottom: 16 }}>
          <Alert tone="warn" title={`${reviewCount} limit(s) need confirmation`}>
            These came across with the QC sheet and the product label disagreeing.
            PIMS uses the QC-sheet value and flags the row rather than choosing quietly.
          </Alert>
        </div>
      )}

      <Tabs
        active={tab}
        onChange={setTab}
        tabs={[
          { key: 'all', label: 'All limits' },
          { key: 'review', label: `Needs confirmation (${reviewCount})` },
          { key: 'tests', label: 'Test lists' },
        ]}
      />

      {tab === 'tests' ? <TestLists /> : (
        <Card
          title="Limits"
          subtitle="Min and max by product and analyte"
          actions={
            <select style={{ width: 220 }} value={family} onChange={(event) => setFamily(event.target.value)}>
              <option value="">All product families</option>
              {families.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          }
          tight
        >
          {specs.loading ? <Loading /> : specs.error ? <ErrorBox error={specs.error} /> : (
            <DataTable
              rows={rows}
              rowKey={(row) => row.spec_id}
              maxHeight="62vh"
              empty="No limits recorded."
              onRowClick={can('spec.read') ? (row) => setEditing(row) : undefined}
              columns={[
                { key: 'number', label: 'Material' },
                { key: 'description', label: 'Description' },
                { key: 'family', label: 'Family' },
                { key: 'analyte', label: 'Analyte', render: (row) => <Badge>{row.analyte}</Badge> },
                { key: 'min_value', label: 'Min', numeric: true, render: (row) => row.min_value ?? '—' },
                { key: 'max_value', label: 'Max', numeric: true, render: (row) => row.max_value ?? '—' },
                { key: 'source', label: 'Source' },
                {
                  key: 'note',
                  label: 'Note',
                  render: (row) => row.needs_review
                    ? <Badge tone="warn">⚠ {row.note || 'confirm'}</Badge>
                    : (row.note || <span className="muted">—</span>),
                },
              ]}
            />
          )}
        </Card>
      )}

      {editing && <EditSpec row={editing} onClose={() => setEditing(null)} onSave={save} />}
    </>
  )
}

function EditSpec({
  row, onClose, onSave,
}: {
  row: SpecRow
  onClose: () => void
  onSave: (row: SpecRow, min: string, max: string, note: string, resolved: boolean) => void
}) {
  const [min, setMin] = useState(row.min_value === null ? '' : String(row.min_value))
  const [max, setMax] = useState(row.max_value === null ? '' : String(row.max_value))
  const [note, setNote] = useState(row.note)
  const [resolved, setResolved] = useState(false)

  return (
    <Modal
      title={`${row.number} · ${row.analyte}`}
      subtitle={row.description}
      width={520}
      onClose={onClose}
      footer={<>
        <button onClick={onClose}>Cancel</button>
        <button className="primary" onClick={() => onSave(row, min, max, note, resolved)}>Save limit</button>
      </>}
    >
      <div className="form-grid cols-2">
        <Field label="Minimum" hint="Blank = no lower bound">
          <input type="number" step="0.01" value={min} onChange={(event) => setMin(event.target.value)} />
        </Field>
        <Field label="Maximum" hint="Blank = no upper bound">
          <input type="number" step="0.01" value={max} onChange={(event) => setMax(event.target.value)} />
        </Field>
        <Field label="Note" className="span-2">
          <input value={note} onChange={(event) => setNote(event.target.value)} />
        </Field>
      </div>
      {row.needs_review ? (
        <div style={{ marginTop: 14 }}>
          <Alert tone="warn" title="This limit is flagged for confirmation">
            {row.note}
          </Alert>
          <label className="row small" style={{ gap: 8, marginTop: 10 }}>
            <input type="checkbox" checked={resolved} onChange={(event) => setResolved(event.target.checked)} />
            The value above is confirmed — clear the flag.
          </label>
        </div>
      ) : null}
      <div className="small muted" style={{ marginTop: 12 }}>
        Changes are written to the audit trail with your username.
      </div>
    </Modal>
  )
}

function TestLists() {
  const { reference } = useApp()
  const [materialId, setMaterialId] = useState<number>(reference.materials[0]?.material_id ?? 0)
  const material = useAsync(() => api.get<any>(`/api/materials/${materialId}`), [materialId])

  return (
    <div className="grid cols-2">
      <Card title="Product">
        <Field label="Material">
          <select value={materialId} onChange={(event) => setMaterialId(Number(event.target.value))}>
            {reference.materials.map((item) => (
              <option key={item.material_id} value={item.material_id}>
                {item.number} — {item.description}
              </option>
            ))}
          </select>
        </Field>
        {material.loading ? <Loading /> : material.error ? <ErrorBox error={material.error} /> : (
          <div className="stack" style={{ marginTop: 14 }}>
            <dl className="kv">
              <dt>Family</dt><dd>{material.data.family || '—'}</dd>
              <dt>Type</dt><dd>{material.data.material_type}</dd>
              <dt>Density</dt><dd>{material.data.density} lbs/gal</dd>
              <dt>Plants</dt><dd>{material.data.plants.map((p: any) => p.code).join(', ') || '—'}</dd>
            </dl>
            <div className="divider" />
            <div>
              <strong className="small">Tested for</strong>
              <div className="row" style={{ gap: 6, marginTop: 6 }}>
                {material.data.tests.length
                  ? material.data.tests.map((analyte: string) => <Badge key={analyte} tone="brand">{analyte}</Badge>)
                  : <span className="muted small">No tests configured — QC will not require any reading.</span>}
              </div>
            </div>
            <Alert tone="info" title="This list drives QC validation">
              A QC record is only asked for the analytes listed here. It is the
              product that decides, not the order type.
            </Alert>
          </div>
        )}
      </Card>

      <Card title="Limits for this product" tight>
        {material.loading ? <Loading /> : (
          <DataTable
            rows={material.data?.specs ?? []}
            rowKey={(row: any) => row.analyte}
            empty="No limits set for this product."
            columns={[
              { key: 'analyte', label: 'Analyte' },
              { key: 'min_value', label: 'Min', numeric: true, render: (row: any) => row.min_value ?? '—' },
              { key: 'max_value', label: 'Max', numeric: true, render: (row: any) => row.max_value ?? '—' },
              { key: 'source', label: 'Source' },
              {
                key: 'note', label: 'Note',
                render: (row: any) => row.needs_review ? <Badge tone="warn">⚠ {row.note}</Badge> : (row.note || '—'),
              },
            ]}
          />
        )}
      </Card>
    </div>
  )
}
