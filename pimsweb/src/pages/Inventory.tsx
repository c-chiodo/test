import { useState } from 'react'
import { useApp } from '../App'
import { api, qs } from '../lib/api'
import type { Balance } from '../lib/types'
import {
  Card, DataTable, ErrorBox, Field, Loading, Meter, Stat, fmtLbs, useAsync,
} from '../components/ui'

export default function Inventory() {
  const { plantId, plantCode, reference } = useApp()
  const [materialId, setMaterialId] = useState<string>('')
  const [locationId, setLocationId] = useState<string>('')
  const [asOf, setAsOf] = useState<string>('')

  const balances = useAsync(
    () => api.get<Balance[]>(`/api/balances${qs({
      plant_id: plantId,
      material_id: materialId,
      location_id: locationId,
      as_of: asOf ? `${asOf}T23:59:59+00:00` : '',
    })}`),
    [plantId, materialId, locationId, asOf],
  )

  const rows = balances.data ?? []
  const total = rows.reduce((sum, row) => sum + row.balance, 0)
  const byMaterial = new Map<string, number>()
  for (const row of rows) {
    byMaterial.set(
      `${row.material_number} ${row.material_description}`,
      (byMaterial.get(`${row.material_number} ${row.material_description}`) ?? 0) + row.balance,
    )
  }
  const topMaterials = [...byMaterial.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10)

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Inventory — {plantCode}</h1>
          <div className="sub">
            Balance by location and material, derived from the transaction ledger.
            {asOf ? ` As of ${asOf}.` : ''}
          </div>
        </div>
      </div>

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <Stat label="Total on hand" value={`${fmtLbs(total)} lbs`} foot={`${rows.length} location/material rows`} />
        <Stat label="Locations in use" value={new Set(rows.map((r) => r.location_id)).size} />
        <Stat label="Materials in stock" value={byMaterial.size} />
        <Stat
          label="Tanks over 85%"
          value={rows.filter((r) => (r.percent_full ?? 0) > 85).length}
          tone={rows.some((r) => (r.percent_full ?? 0) > 95) ? 'warn' : undefined}
        />
      </div>

      <Card title="Filter">
        <div className="form-grid">
          <Field label="Location">
            <select value={locationId} onChange={(event) => setLocationId(event.target.value)}>
              <option value="">All locations</option>
              {reference.locations.filter((l) => l.plant_id === plantId).map((location) => (
                <option key={location.location_id} value={location.location_id}>
                  {location.number} — {location.description}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Material">
            <select value={materialId} onChange={(event) => setMaterialId(event.target.value)}>
              <option value="">All materials</option>
              {reference.materials.map((material) => (
                <option key={material.material_id} value={material.material_id}>
                  {material.number} — {material.description}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="As of date"
            hint="Reconstructs the balance as it stood at the end of that day."
          >
            <input type="date" value={asOf} onChange={(event) => setAsOf(event.target.value)} />
          </Field>
        </div>
      </Card>

      <div className="grid cols-2">
        <Card title="Balances by location" tight>
          {balances.loading ? <Loading /> : balances.error ? <ErrorBox error={balances.error} /> : (
            <DataTable
              rows={rows}
              rowKey={(row) => `${row.location_id}-${row.material_id}`}
              empty="No inventory recorded for this filter."
              maxHeight="58vh"
              columns={[
                { key: 'location_number', label: 'Location' },
                { key: 'location_type', label: 'Type' },
                { key: 'material_number', label: 'Material' },
                { key: 'material_description', label: 'Description' },
                { key: 'balance', label: 'Lbs', numeric: true, render: (row) => fmtLbs(row.balance) },
                { key: 'full', label: 'Capacity', width: 160, render: (row) => <Meter value={row.balance} max={row.max_capacity} /> },
              ]}
            />
          )}
        </Card>

        <Card title="Largest positions" subtitle="Total pounds by material at this plant" tight>
          <DataTable
            rows={topMaterials.map(([material, lbs]) => ({ material, lbs }))}
            rowKey={(row) => row.material}
            empty="Nothing on hand."
            columns={[
              { key: 'material', label: 'Material' },
              { key: 'lbs', label: 'Lbs', numeric: true, render: (row) => fmtLbs(row.lbs) },
              {
                key: 'share',
                label: 'Share',
                width: 150,
                render: (row) => <Meter value={row.lbs} max={total || null} variant="progress" />,
              },
            ]}
          />
        </Card>
      </div>
    </>
  )
}
