/* Custom Query — the query builder, and "Add Matrix Results".
 *
 * Same shape as the legacy tool: pick a data area, pick fields, add where
 * clauses (with prompts), sort, run, export, save. Two differences worth
 * knowing about:
 *   · the SQL that ran is shown, so a question about a number has an answer;
 *   · matrix columns report which cells had no LIMS result and why, instead of
 *     leaving a silent blank. */

import { useMemo, useState } from 'react'
import { useToast } from '../App'
import { api } from '../lib/api'
import type { QueryResult } from '../lib/types'
import {
  Alert, Badge, Card, DataTable, ErrorBox, Field, Loading, Modal, useAsync,
} from '../components/ui'

interface CatalogueField { name: string; label: string; type: string }
interface CatalogueSource { key: string; label: string; description: string; fields: CatalogueField[] }
interface Catalogue { sources: CatalogueSource[]; operators: { key: string; args: unknown }[]; max_rows: number }

interface Filter {
  field: string
  operator: string
  value: string
  logic: 'AND' | 'OR'
  prompt: boolean
}

interface MatrixResult {
  columns: string[]
  rows: Record<string, any>[]
  misses: { sample_code: string; column: string; reason: string }[]
  source: string
}

export default function QueryBuilder() {
  const toast = useToast()
  const catalogue = useAsync(() => api.get<Catalogue>('/api/query/catalogue'), [])
  const saved = useAsync(() => api.get<any[]>('/api/query/saved'), [])

  const [source, setSource] = useState('orders')
  const [fields, setFields] = useState<string[]>([])
  const [filters, setFilters] = useState<Filter[]>([])
  const [sortField, setSortField] = useState('')
  const [sortDirection, setSortDirection] = useState<'ASC' | 'DESC'>('ASC')
  const [limit, setLimit] = useState(500)
  const [result, setResult] = useState<QueryResult | null>(null)
  const [error, setError] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [showSql, setShowSql] = useState(false)
  const [savingOpen, setSavingOpen] = useState(false)
  const [matrix, setMatrix] = useState<MatrixResult | null>(null)
  const [promptOpen, setPromptOpen] = useState<Record<string, string> | null>(null)

  const current = useMemo(
    () => catalogue.data?.sources.find((s) => s.key === source),
    [catalogue.data, source],
  )

  const definition = useMemo(() => ({
    source,
    fields: fields.length ? fields : current?.fields.slice(0, 8).map((f) => f.name) ?? [],
    filters: filters.map((filter) => ({
      field: filter.field,
      operator: filter.operator,
      value: filter.value,
      logic: filter.logic,
      prompt: filter.prompt,
    })),
    sort: sortField ? [{ field: sortField, direction: sortDirection }] : [],
    limit,
  }), [source, fields, filters, sortField, sortDirection, limit, current])

  const prompts = filters.filter((filter) => filter.prompt)

  function chooseSource(key: string) {
    setSource(key)
    setFields([])
    setFilters([])
    setSortField('')
    setResult(null)
    setMatrix(null)
  }

  async function run(promptValues?: Record<string, string>) {
    setBusy(true); setError(null); setMatrix(null)
    try {
      const response = await api.post<QueryResult>('/api/query/run', {
        definition, prompts: promptValues ?? {},
      })
      setResult(response)
      setPromptOpen(null)
      if (response.truncated) {
        toast.push('info', 'Result truncated', `Showing the first ${response.row_count} rows.`)
      }
    } catch (err) { setError(err) } finally { setBusy(false) }
  }

  function execute() {
    if (prompts.length) {
      setPromptOpen(Object.fromEntries(prompts.map((filter) => [filter.field, ''])))
      return
    }
    run()
  }

  async function exportCsv() {
    try {
      await api.download('/api/query/run/csv', { definition, prompts: {} }, 'pims-query.csv')
    } catch (err) { toast.push('error', 'Export failed', (err as Error).message) }
  }

  async function addMatrix() {
    if (!result) return
    const sampleColumn = result.columns.find((column) => column.name === 'sample_number')
    if (!sampleColumn) {
      toast.push('error', 'No sample number column', 'Add "Sample #" to the query first.')
      return
    }
    const codes = result.rows.map((row) => row.sample_number).filter(Boolean)
    if (!codes.length) {
      toast.push('error', 'No sample numbers in the result set')
      return
    }
    try {
      const response = await api.post<MatrixResult>('/api/lims/matrix', { sample_codes: codes })
      setMatrix(response)
      toast.push(
        'success',
        `${response.columns.length} LIMS column(s) added`,
        response.misses.length ? `${response.misses.length} cell(s) had no result — see the note below the grid.` : undefined,
      )
    } catch (err) { toast.push('error', 'Matrix failed', (err as Error).message) }
  }

  async function saveQuery(name: string, description: string) {
    try {
      await api.post('/api/query/saved', { name, description, definition })
      toast.push('success', `Saved "${name}"`)
      setSavingOpen(false)
      saved.reload()
    } catch (err) { toast.push('error', 'Save failed', (err as Error).message) }
  }

  async function loadQuery(queryId: number) {
    const record = await api.get<any>(`/api/query/saved/${queryId}`)
    const loaded = record.definition
    setSource(loaded.source)
    setFields(loaded.fields ?? [])
    setFilters((loaded.filters ?? []).map((filter: any) => ({
      field: filter.field, operator: filter.operator ?? '=', value: filter.value ?? '',
      logic: filter.logic ?? 'AND', prompt: Boolean(filter.prompt),
    })))
    setSortField(loaded.sort?.[0]?.field ?? '')
    setSortDirection(loaded.sort?.[0]?.direction ?? 'ASC')
    setLimit(loaded.limit ?? 500)
    setResult(null)
    setMatrix(null)
  }

  if (catalogue.loading) return <Loading />
  if (catalogue.error) return <ErrorBox error={catalogue.error} />

  const mergedRows = matrix
    ? (result?.rows ?? []).map((row) => ({
        ...row,
        ...(matrix.rows.find((m) => m.sample_code === row.sample_number) ?? {}),
      }))
    : result?.rows ?? []

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Custom query</h1>
          <div className="sub">Build a report, run it, add lab results, export it.</div>
        </div>
        <div className="actions">
          <select
            style={{ width: 240 }}
            value=""
            onChange={(event) => event.target.value && loadQuery(Number(event.target.value))}
          >
            <option value="">Open a saved query…</option>
            {(saved.data ?? []).map((query) => (
              <option key={query.query_id} value={query.query_id}>{query.name}</option>
            ))}
          </select>
          <button onClick={() => setSavingOpen(true)}>Save query</button>
          <button className="primary" onClick={execute} disabled={busy}>
            {busy ? <span className="spinner" /> : null} Execute
          </button>
        </div>
      </div>

      <div className="grid cols-2">
        <Card title="Data area">
          <div className="stack">
            <Field label="Table family">
              <select value={source} onChange={(event) => chooseSource(event.target.value)}>
                {catalogue.data!.sources.map((item) => (
                  <option key={item.key} value={item.key}>{item.label}</option>
                ))}
              </select>
            </Field>
            <div className="small muted">{current?.description}</div>
            <div className="divider" />
            <div className="row" style={{ gap: 6 }}>
              <strong className="small">Fields</strong>
              <span className="muted small">({fields.length || 'default set'})</span>
              <div className="spacer" style={{ flex: 1 }} />
              <button className="ghost sm" onClick={() => setFields(current?.fields.map((f) => f.name) ?? [])}>
                Select all
              </button>
              <button className="ghost sm" onClick={() => setFields([])}>Clear</button>
            </div>
            <div style={{ maxHeight: 220, overflow: 'auto' }}>
              {current?.fields.map((field) => (
                <label key={field.name} className="row small" style={{ gap: 8, padding: '3px 0' }}>
                  <input
                    type="checkbox"
                    checked={fields.includes(field.name)}
                    onChange={(event) => setFields((currentFields) =>
                      event.target.checked
                        ? [...currentFields, field.name]
                        : currentFields.filter((name) => name !== field.name),
                    )}
                  />
                  {field.label}
                  <span className="muted mono" style={{ marginLeft: 'auto' }}>{field.type}</span>
                </label>
              ))}
            </div>
          </div>
        </Card>

        <Card
          title="Filters and sort"
          actions={
            <button
              className="sm"
              onClick={() => setFilters((current) => [...current, {
                field: currentFirstField(catalogue.data!, source),
                operator: '=', value: '', logic: 'AND', prompt: false,
              }])}
            >
              Add filter
            </button>
          }
        >
          <div className="stack">
            {filters.length === 0 && <div className="muted small">No filters — every row is returned.</div>}
            {filters.map((filter, index) => (
              <div key={index} className="row" style={{ gap: 8, alignItems: 'flex-end' }}>
                {index > 0 && (
                  <select
                    style={{ width: 76 }}
                    value={filter.logic}
                    onChange={(event) => updateFilter(setFilters, index, { logic: event.target.value as 'AND' | 'OR' })}
                  >
                    <option>AND</option>
                    <option>OR</option>
                  </select>
                )}
                <select
                  style={{ flex: 2 }}
                  value={filter.field}
                  onChange={(event) => updateFilter(setFilters, index, { field: event.target.value })}
                >
                  {current?.fields.map((field) => (
                    <option key={field.name} value={field.name}>{field.label}</option>
                  ))}
                </select>
                <select
                  style={{ width: 120 }}
                  value={filter.operator}
                  onChange={(event) => updateFilter(setFilters, index, { operator: event.target.value })}
                >
                  {catalogue.data!.operators.map((operator) => (
                    <option key={operator.key} value={operator.key}>{operator.key}</option>
                  ))}
                </select>
                <input
                  style={{ flex: 2 }}
                  placeholder={filter.prompt ? '{PROMPT}' : 'value ( % is the wildcard )'}
                  value={filter.prompt ? '' : filter.value}
                  disabled={filter.prompt || filter.operator.startsWith('IS ')}
                  onChange={(event) => updateFilter(setFilters, index, { value: event.target.value })}
                />
                <label className="row small nowrap" style={{ gap: 4 }}>
                  <input
                    type="checkbox"
                    checked={filter.prompt}
                    onChange={(event) => updateFilter(setFilters, index, { prompt: event.target.checked })}
                  />
                  Prompt
                </label>
                <button
                  className="ghost sm"
                  onClick={() => setFilters((current) => current.filter((_, i) => i !== index))}
                >
                  ✕
                </button>
              </div>
            ))}

            <div className="divider" />
            <div className="row" style={{ gap: 10 }}>
              <Field label="Sort by">
                <select value={sortField} onChange={(event) => setSortField(event.target.value)}>
                  <option value="">No sort</option>
                  {current?.fields.map((field) => (
                    <option key={field.name} value={field.name}>{field.label}</option>
                  ))}
                </select>
              </Field>
              <Field label="Direction">
                <select value={sortDirection} onChange={(event) => setSortDirection(event.target.value as 'ASC' | 'DESC')}>
                  <option>ASC</option>
                  <option>DESC</option>
                </select>
              </Field>
              <Field label="Row limit">
                <input type="number" value={limit} onChange={(event) => setLimit(Number(event.target.value))} />
              </Field>
            </div>
          </div>
        </Card>
      </div>

      {error && <div style={{ marginTop: 16 }}><ErrorBox error={error} /></div>}

      <Card
        title="Results"
        subtitle={result ? `${result.row_count.toLocaleString()} record(s) returned` : 'Run the query to see results'}
        actions={result ? (
          <div className="row" style={{ gap: 8 }}>
            <button className="sm" onClick={() => setShowSql((value) => !value)}>
              {showSql ? 'Hide SQL' : 'Show SQL'}
            </button>
            <button className="sm" onClick={addMatrix}>Add matrix results</button>
            <button className="sm" onClick={exportCsv}>Export to CSV</button>
          </div>
        ) : undefined}
        tight
      >
        {showSql && result && (
          <div style={{ padding: 16, paddingBottom: 0 }}>
            <div className="sql-box">{result.sql}</div>
            <div className="small muted" style={{ marginTop: 6 }}>
              Parameters: {JSON.stringify(result.parameters)}
            </div>
          </div>
        )}

        {matrix && (
          <div style={{ padding: 16, paddingBottom: 0 }}>
            <Alert
              tone={matrix.misses.length ? 'warn' : 'ok'}
              title={`LIMS columns from ${matrix.source}`}
            >
              {matrix.misses.length
                ? `${matrix.misses.length} cell(s) are blank because no result is recorded for that sample and component — not because the lookup failed.`
                : 'Every requested cell had a current, reportable result.'}
            </Alert>
          </div>
        )}

        {busy ? <Loading /> : !result ? (
          <div className="empty">Choose fields and filters, then Execute.</div>
        ) : (
          <DataTable
            rows={mergedRows}
            rowKey={(_row, index) => index}
            maxHeight="55vh"
            empty="No records matched."
            columns={[
              ...result.columns.map((column) => ({
                key: column.name,
                label: column.label,
                numeric: column.type === 'number',
              })),
              ...(matrix?.columns ?? []).map((column) => ({
                key: column,
                label: column,
                numeric: true,
                render: (row: any) => row[column] ?? <span className="muted">—</span>,
              })),
            ]}
          />
        )}
      </Card>

      {savingOpen && <SaveDialog onClose={() => setSavingOpen(false)} onSave={saveQuery} />}

      {promptOpen && (
        <Modal
          title="Query prompts"
          subtitle="This query asks for its values each time it runs."
          width={480}
          onClose={() => setPromptOpen(null)}
          footer={<>
            <button onClick={() => setPromptOpen(null)}>Cancel</button>
            <button className="primary" onClick={() => run(promptOpen)}>Run</button>
          </>}
        >
          <div className="stack">
            {prompts.map((filter) => (
              <Field key={filter.field} label={`${labelFor(current, filter.field)} ${filter.operator}`}>
                <input
                  value={promptOpen[filter.field] ?? ''}
                  onChange={(event) => setPromptOpen((current) => ({ ...current!, [filter.field]: event.target.value }))}
                />
              </Field>
            ))}
          </div>
        </Modal>
      )}
    </>
  )
}

function SaveDialog({ onClose, onSave }: { onClose: () => void; onSave: (name: string, description: string) => void }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  return (
    <Modal
      title="Save query"
      width={480}
      onClose={onClose}
      footer={<>
        <button onClick={onClose}>Cancel</button>
        <button className="primary" disabled={!name.trim()} onClick={() => onSave(name, description)}>Save</button>
      </>}
    >
      <div className="stack">
        <Field label="Report name"><input value={name} onChange={(e) => setName(e.target.value)} autoFocus /></Field>
        <Field label="Description"><input value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        <Badge tone="info">Saved queries are shared with everyone who can run queries.</Badge>
      </div>
    </Modal>
  )
}

function updateFilter(
  setFilters: React.Dispatch<React.SetStateAction<Filter[]>>,
  index: number,
  patch: Partial<Filter>,
) {
  setFilters((current) => current.map((filter, i) => (i === index ? { ...filter, ...patch } : filter)))
}

function currentFirstField(catalogue: Catalogue, source: string): string {
  return catalogue.sources.find((s) => s.key === source)?.fields[0]?.name ?? ''
}

function labelFor(source: CatalogueSource | undefined, field: string): string {
  return source?.fields.find((f) => f.name === field)?.label ?? field
}
