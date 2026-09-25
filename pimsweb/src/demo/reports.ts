/* The reports, re-implemented for the browser-only sandbox. Mirrors
 * `pims/services/reports.py` — same material roles, same definitions, same
 * JSON — so the Reports page cannot tell the difference. */

import { Row, byId, store } from './store'

const MATERIALS: Record<string, number[]> = {
  soap: [6, 7, 10], acid: [1], steam: [4], water_in: [11, 15, 1008], process: [1006], mgr: [1007],
  mgr_animal: [1003], oil: [1017, 1018, 1019], process_water: [1008], caustic: [3],
  out_mgrv: [1007, 3017, 3018, 3019], out_mgra: [1003, 5003],
}
const WATER_TRAILER_LBS = 46_000
const LEGACY_TYPE_NAMES: Record<string, string> = {
  RECEIVE: 'RECEIVED', PRODUCE: 'PRODUCED', MOVE: 'MOVEMENT', LOAD: 'MOVE-LOAD',
  PROD_LOAD: 'PROD-LOAD', SHIP: 'SHIP-LEAVE', SHRINK: 'SHRINKAGE', ADJUST: 'SHIPADJ',
}

function invalid(message: string, fields: Record<string, string>): never {
  throw { status: 422, code: 'validation_error', message, detail: { fields } }
}

function roles(): Record<string, Set<number>> {
  const out: Record<string, number[]> = { ...MATERIALS }
  const raw = store.system_setting.find((s) => s.key === 'reports.materials')?.value
  if (raw) {
    try {
      for (const [k, v] of Object.entries(JSON.parse(raw))) if (Array.isArray(v)) out[k] = v.map(Number)
    } catch { /* keep the defaults */ }
  }
  return Object.fromEntries(Object.entries(out).map(([k, v]) => [k, new Set(v)]))
}

const num = (n: unknown): number | null => {
  const v = parseInt(String(n ?? '').trim(), 10)
  return Number.isFinite(v) && /^\s*\d+\s*$/.test(String(n ?? '')) ? v : null
}
const r1 = (x: number) => Math.round(x * 10) / 10
const pct = (part: number, whole: number) => (whole ? Math.round((part / whole) * 10000) / 100 : null)
const isoDay = (d: Date) => d.toISOString().slice(0, 10)

function period(f: Row): [string, string] {
  const end = String(f.end || isoDay(new Date())).slice(0, 10)
  const start = String(f.start || isoDay(new Date(new Date(end + 'T00:00:00Z').getTime() - 27 * 86_400_000))).slice(0, 10)
  if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end)) invalid('Dates must be YYYY-MM-DD.', { start: 'Use YYYY-MM-DD.' })
  if (start > end) invalid('The start date is after the end date.', { start: 'Pick an earlier start.' })
  if ((Date.parse(end) - Date.parse(start)) / 86_400_000 > 400) invalid('Report at most 400 days at a time.', { start: 'Pick a later start.' })
  return [start, end]
}

function plantsFor(f: Row, user: Row): Row[] {
  const all = store.plant.filter((p) => p.active !== 0).sort((a, b) => a.code.localeCompare(b.code))
  const allowed = user.role === 'admin' ? all
    : all.filter((p) => store.user_plant_access.some((a) => a.user_id === user.user_id && a.plant_id === p.plant_id))
  if (f.plant_id === undefined || f.plant_id === null || f.plant_id === '' || f.plant_id === 'all' || f.plant_id === 0) return allowed
  const id = Number(f.plant_id)
  if (!allowed.some((p) => p.plant_id === id)) throw { status: 403, code: 'forbidden', message: 'You do not have access to that plant.', detail: { plant_id: id } }
  return allowed.filter((p) => p.plant_id === id)
}

function ledger(plantIds: number[], start: string, end: string, includeReversed = false): Row[] {
  const materials = byId.material(), locations = byId.location(), types = byId.transactionType()
  const plants = byId.plant(), users = byId.user(), orders = byId.order(), vendors = byId.vendor()
  const departments = byId.department()
  const ltypes = new Map(store.location_type.map((t) => [t.location_type_id, t.name]))
  const ids = new Set(plantIds)
  const txnById = new Map(store.inventory_transaction.map((t) => [t.transaction_id, t]))
  const out: Row[] = []
  for (const t of store.inventory_transaction) {
    if (!ids.has(t.plant_id)) continue
    const day = String(t.user_date).slice(0, 10)
    if (day < start || day > end) continue
    if (!includeReversed && (t.voided || t.is_reversal)) continue
    out.push(shape(t))
  }
  return out.sort((a, b) => a.transaction_id - b.transaction_id)

  function shape(t: Row): Row {
    const fm = materials.get(t.from_material_id), tm = materials.get(t.to_material_id)
    const fl = locations.get(t.from_location_id), tl = locations.get(t.to_location_id)
    const tt = types.get(t.transaction_type_id) ?? {}
    const o = orders.get(t.order_id)
    const parent = t.parent_transaction_id ? txnById.get(t.parent_transaction_id) : undefined
    const parentType = parent ? types.get(parent.transaction_type_id) : undefined
    const v = o ? vendors.get(o.vendor_id) : undefined
    return {
      ...t, plant_code: plants.get(t.plant_id)?.code, type_code: tt.code, kind: tt.kind ?? tt.code, type_name: tt.description,
      from_number: fm?.number ?? null, from_description: fm?.description ?? null,
      from_location: fl?.number ?? null, from_location_type: fl ? ltypes.get(fl.location_type_id) : null,
      to_number: tm?.number ?? null, to_description: tm?.description ?? null,
      to_location: tl?.number ?? null, to_location_type: tl ? ltypes.get(tl.location_type_id) : null,
      parent_kind: parentType ? parentType.kind ?? parentType.code : null,
      department_code: departments.get(t.department_id)?.code ?? null, user_name: users.get(t.user_id)?.full_name ?? null,
      order_reference: o?.order_reference ?? null, ship_method: o?.ship_method ?? null,
      gp_vendorid: v?.gp_vendorid ?? null, vendor_name: v?.name ?? null,
      day: String(t.user_date).slice(0, 10), f: num(fm?.number), t: num(tm?.number),
      from_qty: Number(t.from_qty || 0), to_qty: Number(t.to_qty || 0),
    }
  }
}

export function ledgerRow(id: number): Row | undefined {
  const t = store.inventory_transaction.find((x) => x.transaction_id === id)
  return t ? ledger([t.plant_id], '0000-00-00', '9999-99-99', true).find((x) => x.transaction_id === id) : undefined
}

const YIELD_DEFINITIONS: [string, string][] = [
  ['Soap received', 'Soap (Soap - Gum, Soap - Degum, Wetgums) received, by the pound on the receipt.'],
  ['Soap processed', 'Soap charged into Soap in Process: the soap that went into a settle.'],
  ['Acid used', 'Acid charged into Soap in Process; % is per pound of soap processed.'],
  ['Steam (est.)', 'Steam charged into Soap in Process, as the operator estimated it.'],
  ['Reprocessed water', 'Process or city water charged back into Soap in Process.'],
  ['Settle break', "What each settle drew off: 20's oil, MGR and water, and each as a share of the three."],
  ['MGR break', "What reprocessing MGR drew off: oil to the 20's, MGR kept back, and water. MGR processed is what went into the MGR tanks to be reprocessed."],
  ["Total 20's oil", 'All oil made into the 20-series tanks, from settles and MGR.'],
  ["20's bottoms", "20's oil sent back to MGR."],
  ['Oil final', "20's oil made or loaded into a finished product."],
  ['FPY', 'First-pass yield: settle oil ÷ (soap processed × TFA).'],
  ['SPY', "Second-pass yield: 1 − 20's bottoms ÷ total 20's oil."],
  ['OY', 'Overall yield: oil final ÷ (soap processed × TFA). The second figure leaves reprocessed water out of the soap.'],
  ['Outbound', 'Pounds of MGRV, MGRA, process water and caustic blended onto trailers.'],
  ['Outbound water', 'Process water shipped; trailers estimated at 46,000 lbs each.'],
]

function acidYields(f: Row, user: Row): Row {
  const [start, end] = period(f)
  const plants = plantsFor(f, user)
  const tfa = Number(f.tfa || 26)
  const m = roles()
  const rows = ledger(plants.map((p) => p.plant_id), start, end)
  const totals: Record<string, number> = {}
  const daily: Record<string, Record<string, number>> = {}
  const add = (key: string, qty: number, day: string) => {
    totals[key] = (totals[key] ?? 0) + qty
    daily[day] = daily[day] ?? {}
    daily[day][key] = (daily[day][key] ?? 0) + qty
  }
  const T = (k: string) => totals[k] ?? 0
  const has = (set: string, n: number | null) => n !== null && m[set].has(n)
  // Which tanks reprocess MGR: by type, or where MGR is regularly broken into oil.
  const brokenFrom = new Map<number, Map<number, number>>()
  for (const r of rows) {
    if (r.kind === 'PRODUCE' && has('mgr', r.f) && has('oil', r.t) && r.from_location_id) {
      const counts = brokenFrom.get(r.plant_id) ?? new Map<number, number>()
      counts.set(r.from_location_id, (counts.get(r.from_location_id) ?? 0) + 1)
      brokenFrom.set(r.plant_id, counts)
    }
  }
  const vessels = new Set<number>()
  for (const counts of brokenFrom.values()) {
    const top = Math.max(...counts.values())
    for (const [loc, n] of counts) if (n >= 0.2 * top) vessels.add(loc)
  }
  const mgrVessel = (id: number | null, type: string | null) => type === 'MGR' || (id !== null && vessels.has(id))
  for (const r of rows) {
    const { f: fn, t: tn, kind, day } = r
    if (kind === 'RECEIVE' && has('soap', tn)) add('soap_received', r.to_qty, day)
    if ((kind === 'PRODUCE' || kind === 'MOVE') && has('process', tn)) {
      if (has('soap', fn)) add('soap_processed', r.from_qty, day)
      else if (has('acid', fn)) add('acid_used', r.from_qty, day)
      else if (has('steam', fn)) add('steam', r.from_qty, day)
      else if (has('water_in', fn)) add('reprocessed_water', r.from_qty, day)
    }
    if (kind === 'PRODUCE') {
      if (has('process', fn)) {
        if (has('oil', tn)) add('settle_oil', r.to_qty, day)
        else if (has('mgr', tn)) add('settle_mgr', r.to_qty, day)
        else if (has('process_water', tn)) add('settle_water', r.to_qty, day)
      } else if (has('mgr', fn)) {
        if (has('oil', tn)) add('mgr_oil', r.to_qty, day)
        else if (has('mgr', tn) && !mgrVessel(r.to_location_id, r.to_location_type) && r.from_location_id !== r.to_location_id) add('mgr_mgr', r.to_qty, day)
        else if (has('process_water', tn)) add('mgr_water', r.to_qty, day)
      } else if (has('mgr_animal', fn) && has('oil', tn)) add('mgra_oil', r.to_qty, day)
      if (has('mgr', tn) && mgrVessel(r.to_location_id, r.to_location_type) && !mgrVessel(r.from_location_id, r.from_location_type)) add('mgr_processed', r.to_qty, day)
      if (has('oil', tn)) add('total_oil', r.to_qty, day)
      if (has('oil', fn) && (has('mgr', tn) || has('mgr_animal', tn))) add('bottoms', r.to_qty, day)
    }
    if (has('oil', fn) && (kind === 'PRODUCE' || kind === 'LOAD') && tn !== null
      && !has('oil', tn) && !has('mgr', tn) && !has('mgr_animal', tn) && !has('process_water', tn)) add('oil_final', r.to_qty, day)
    if (kind === 'LOAD' && fn !== null && tn !== null && fn !== tn) {
      if (has('out_mgrv', fn)) add('out_mgrv', r.from_qty, day)
      else if (has('out_mgra', fn)) add('out_mgra', r.from_qty, day)
      else if (has('process_water', fn)) add('out_water', r.from_qty, day)
      else if (has('caustic', fn)) add('out_caustic', r.from_qty, day)
    }
    if (kind === 'SHIP' && has('process_water', fn)) add('water_shipped', r.from_qty, day)
    if (kind === 'ADJUST' && r.parent_kind === 'SHIP' && has('process_water', fn)) add('water_shipped', r.from_qty, day)
  }
  const theoretical = T('soap_processed') * tfa / 100
  const netSoap = T('soap_processed') - T('reprocessed_water')
  const split = (parts: Record<string, number>) => {
    const whole = Object.values(parts).reduce((a, b) => a + b, 0)
    return Object.entries(parts).map(([part, v]) => ({ part, lbs: r1(v), pct: pct(v, whole) }))
  }
  const series: Row[] = []
  let window: Record<string, number>[] = []
  for (let d = new Date(start + 'T00:00:00Z'); isoDay(d) <= end; d = new Date(d.getTime() + 86_400_000)) {
    const today = daily[isoDay(d)] ?? {}
    window = [...window, today].slice(-7)
    const sum = (k: string) => window.reduce((a, w) => a + (w[k] ?? 0), 0)
    const soap7 = sum('soap_processed')
    series.push({
      date: isoDay(d), soap_processed: r1(today.soap_processed ?? 0), acid_used: r1(today.acid_used ?? 0),
      oil_fp: r1(today.settle_oil ?? 0), oil_final: r1(today.oil_final ?? 0),
      fpy_7d: pct(sum('settle_oil'), soap7 * tfa / 100), oy_7d: pct(sum('oil_final'), soap7 * tfa / 100),
    })
  }
  return {
    report: 'acid-yields', start, end, tfa, plants: plants.map((p) => p.code),
    inputs: {
      soap_received: r1(T('soap_received')), soap_processed: r1(T('soap_processed')), soap_processed_less_water: r1(netSoap),
      acid_used: r1(T('acid_used')), acid_pct: pct(T('acid_used'), T('soap_processed')), steam: r1(T('steam')),
      reprocessed_water: r1(T('reprocessed_water')),
    },
    settle_break: split({ oil: T('settle_oil'), mgr: T('settle_mgr'), water: T('settle_water') }),
    settle_total: r1(T('settle_oil') + T('settle_mgr') + T('settle_water')),
    mgr_break: split({ oil: T('mgr_oil'), mgr: T('mgr_mgr'), water: T('mgr_water') }),
    mgr_total: r1(T('mgr_oil') + T('mgr_mgr') + T('mgr_water')),
    mgr_processed: r1(T('mgr_processed')),
    oil: {
      total_20s: r1(T('total_oil')), settle_oil: r1(T('settle_oil')), mgrv_oil: r1(T('mgr_oil')), mgra_oil: r1(T('mgra_oil')),
      bottoms_20s: r1(T('bottoms')), bottoms_pct: pct(T('bottoms'), T('total_oil')), oil_final: r1(T('oil_final')),
    },
    yields: {
      theoretical_oil: r1(theoretical), fpy: pct(T('settle_oil'), theoretical),
      spy: T('total_oil') ? Math.round((100 - (T('bottoms') / T('total_oil')) * 100) * 100) / 100 : null,
      oy: pct(T('oil_final'), theoretical), oy_less_water: pct(T('oil_final'), netSoap * tfa / 100),
    },
    outbound: { mgrv: r1(T('out_mgrv')), mgra: r1(T('out_mgra')), water: r1(T('out_water')), caustic: r1(T('out_caustic')) },
    outbound_water: { lbs: r1(T('water_shipped')), trailers_est: Math.round((T('water_shipped') / WATER_TRAILER_LBS) * 10) / 10 },
    daily: series,
    definitions: YIELD_DEFINITIONS.map(([name, definition]) => ({ name, definition })),
  }
}

function orderPh(orderIds: number[]): Map<number, number[]> {
  const wanted = new Set(orderIds.filter(Boolean))
  const found = new Map<number, number[]>()
  const push = (id: number, v: number) => {
    const list = found.get(id) ?? []
    const value = Math.round(v * 100) / 100
    if (!list.includes(value)) list.push(value)
    found.set(id, list)
  }
  const txns = new Map(store.inventory_transaction.filter((t) => wanted.has(t.order_id) && !t.voided && !t.is_reversal).map((t) => [t.transaction_id, t]))
  for (const r of [...store.txn_reading].sort((a, b) => a.transaction_id - b.transaction_id)) {
    const t = txns.get(r.transaction_id)
    if (t && r.analyte === 'ph' && r.value) push(t.order_id, Number(r.value))
  }
  for (const q of [...store.qc].sort((a, b) => String(a.test_date).localeCompare(String(b.test_date)))) {
    if (q.active !== 0 && wanted.has(q.order_id) && q.ph) push(q.order_id, Number(q.ph))
  }
  return found
}

function caustic(f: Row, user: Row): Row {
  const [start, end] = period(f)
  const plants = plantsFor(f, user)
  const m = roles()
  const rows = ledger(plants.map((p) => p.plant_id), start, end, true)
    .filter((r) => r.kind === 'LOAD' && !r.is_reversal && r.f !== null && m.caustic.has(r.f) && !(r.t !== null && m.caustic.has(r.t)))
  const perOrder = new Map<any, Row>()
  for (const r of rows) {
    const key = r.order_id ?? `txn-${r.transaction_id}`
    const o = perOrder.get(key) ?? {
      transaction_date: r.transaction_date, day: r.day, plant: r.plant_code, product_number: r.to_number,
      product: r.to_description, order_id: r.order_id, order_reference: r.order_reference ?? '',
      gross: 0, reversed: 0, postings: 0,
    }
    o.gross += r.from_qty
    o.postings += 1
    if (r.voided) o.reversed += r.from_qty
    perOrder.set(key, o)
  }
  const ph = orderPh([...perOrder.values()].map((o) => o.order_id))
  const loads: Row[] = [...perOrder.values()].map((o): Row => {
    const readings = o.order_id ? ph.get(o.order_id) ?? [] : []
    const net = o.gross - o.reversed
    return {
      ...o, gross: r1(o.gross), reversed: r1(o.reversed), net: r1(net), ph_readings: readings,
      ph: readings.length ? readings[readings.length - 1] : null,
      status: !o.reversed ? 'No reversal' : net <= 0.5 ? 'Fully reversed' : 'Reversed and reposted',
    }
  }).sort((a, b) => `${a.plant}|${a.product}|${a.transaction_date}`.localeCompare(`${b.plant}|${b.product}|${b.transaction_date}`))
  const summarise = (group: Row[]) => {
    const gross = group.reduce((a, o) => a + o.gross, 0), rev = group.reduce((a, o) => a + o.reversed, 0)
    const tested = group.filter((o) => o.ph !== null).map((o) => o.ph)
    const counted = group.filter((o) => o.net > 0.5)
    return {
      loads: counted.length, gross: r1(gross), reversed: r1(rev), net: r1(gross - rev), reversal_pct: pct(rev, gross),
      avg_per_load: counted.length ? r1((gross - rev) / counted.length) : null,
      avg_ph: tested.length ? Math.round((tested.reduce((a, b) => a + b, 0) / tested.length) * 100) / 100 : null,
      not_tested: counted.filter((o) => o.ph === null).length,
    }
  }
  const group = (key: (o: Row) => string) => {
    const map = new Map<string, Row[]>()
    for (const o of loads) map.set(key(o), [...(map.get(key(o)) ?? []), o])
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b))
  }
  return {
    report: 'caustic', start, end, plants: plants.map((p) => p.code),
    total: summarise(loads),
    by_plant: group((o) => o.plant).map(([k, v]) => ({ plant: k, ...summarise(v) })),
    by_product: group((o) => `${o.plant}\u0000${o.product}`).map(([k, v]) => ({ plant: k.split('\u0000')[0], product: k.split('\u0000')[1], ...summarise(v) })),
    by_month: group((o) => `${o.day.slice(0, 7)}\u0000${o.plant}`).map(([k, v]) => ({ month: k.split('\u0000')[0], plant: k.split('\u0000')[1], ...summarise(v) })),
    loads,
    definitions: [
      { name: 'Gross', definition: 'Every caustic posting blended onto a trailer, including those later reversed.' },
      { name: 'Reversed', definition: 'Caustic postings that were reversed (the legacy PROD-LOAD - REVERSAL rows).' },
      { name: 'Net', definition: 'Gross less reversed: the caustic that went out on the truck.' },
      { name: 'pH', definition: 'The last pH on the load — the dosed reading if the load was blended here, otherwise the QC result. A legacy 0 means not tested and is not averaged.' },
    ],
  }
}

function reversals(f: Row, user: Row): Row {
  const [start, end] = period(f)
  const plants = plantsFor(f, user)
  const rows = ledger(plants.map((p) => p.plant_id), start, end, true).filter((r) => !r.is_reversal)
  const label = (r: Row) => LEGACY_TYPE_NAMES[r.type_code] ?? r.type_name ?? r.type_code
  const lbs = (group: Row[]) => r1(group.reduce((a, r) => a + Math.max(r.from_qty, r.to_qty), 0))
  const summarise = (group: Row[]) => {
    const undone = group.filter((r) => r.voided)
    return { postings: group.length, reversed: undone.length, rate_pct: pct(undone.length, group.length), lbs_reversed: lbs(undone) }
  }
  const bucket = (key: (r: Row) => string, source = rows) => {
    const map = new Map<string, Row[]>()
    for (const r of source) map.set(key(r), [...(map.get(key(r)) ?? []), r])
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b))
  }
  const monday = (day: string) => {
    const d = new Date(day + 'T00:00:00Z')
    return isoDay(new Date(d.getTime() - ((d.getUTCDay() + 6) % 7) * 86_400_000))
  }
  return {
    report: 'reversals', start, end, plants: plants.map((p) => p.code),
    total: summarise(rows),
    by_type: bucket((r) => `${r.plant_code}\u0000${label(r)}`).map(([k, v]) => ({ plant: k.split('\u0000')[0], type: k.split('\u0000')[1], ...summarise(v) })),
    by_week: bucket((r) => monday(r.day)).map(([k, v]) => ({ week: k, ...summarise(v) })),
    top_materials: bucket((r) => `${r.plant_code}\u0000${label(r)}\u0000${r.from_description || r.to_description || ''}`, rows.filter((r) => r.voided))
      .map(([k, v]) => { const [plant, type, material] = k.split('\u0000'); return { plant, type, material, reversed: v.length, lbs_reversed: lbs(v) } })
      .sort((a, b) => b.lbs_reversed - a.lbs_reversed).slice(0, 15),
  }
}

const LAYOUTS: Record<string, { label: string; columns: string[] }> = {
  query: {
    label: 'PIMS QUERY Export (30 columns — the operations workbook)',
    columns: ['Transaction_Date', 'User_Date', 'Full_TransType_Name', 'Order_Id', 'From_Material_Number',
      'From_Material_Description', 'From_Location_Plant_Code', 'From_Location_Number', 'From_BOL_Number',
      'From_QC_Moisture', 'From_QC_Temp', 'From_QC_Ph', 'From_QC_Spintest_Fallout', 'From_QC_Sample_Number',
      'From_Qty', 'To_Material_Number', 'To_Material_Description', 'To_Location_Plant_Code',
      'To_Location_Number', 'To_BOL_Number', 'To_QC_Moisture', 'To_QC_Temp', 'To_QC_Ph',
      'To_QC_Spintest_Fallout', 'To_QC_Sample_Number', 'To_Qty', 'User_Name', 'Transaction_Remarks',
      'Order_Reference', 'Ship_Method'],
  },
  yields: {
    label: 'PIMS QUERY Export (28 columns — DM Yields)',
    columns: ['Transaction_Date', 'From_Location_Plant_Code', 'To_Location_Plant_Code', 'Order_Id', 'Order_Reference',
      'Full_TransType_Name', 'GP_Vendor_ID', 'GP_Vendor_Name', 'From_Material_Number',
      'From_Material_Description', 'From_Location_Number', 'From_BOL_Number', 'From_QC_Moisture',
      'From_QC_Temp', 'From_QC_Ph', 'From_Qty', 'To_Material_Number', 'To_Material_Description',
      'To_Location_Number', 'To_Qty', 'To_BOL_Number', 'To_QC_Test_Date', 'To_QC_Moisture', 'To_QC_Temp',
      'To_QC_Ph', 'To_QC_Spintest_Fallout', 'User_Name', 'Transaction_Remarks'],
  },
  report: {
    label: 'PIMS Report Export (24 columns — caustic, MGR and pH)',
    columns: ['Trans Id', 'Parent', 'Trans Date', 'User Date', 'Type', 'Ord', 'Plant', 'From Mat', 'From Mat Desc',
      'From Loc', 'From BOL', 'From Qty', 'To Mat', 'To Mat Desc', 'To Loc', 'To BOL', 'To Qty', 'User',
      'Dept', 'From Loc Hr', 'Emp Hr', 'Seal', 'Remarks', 'Comments'],
  },
}

function legacyLocation(number: unknown, plant: string): any {
  if (!number) return null
  let text = String(number)
  if (text.toUpperCase().startsWith(plant.toUpperCase() + '-')) text = text.slice(plant.length + 1)
  return /^\d+$/.test(text) ? Number(text) : text
}

function stamp(value: unknown, dateOnly = false): string {
  const text = String(value ?? '')
  if (!text) return ''
  if (dateOnly) return text.slice(0, 10)
  const d = new Date(text)
  return Number.isNaN(d.getTime()) ? text : d.toISOString().slice(0, 19).replace('T', ' ')
}

function exportLayout(f: Row, user: Row): Row {
  const layout = String(f.layout || 'query')
  if (!LAYOUTS[layout]) invalid('Unknown export layout.', { layout: `One of ${Object.keys(LAYOUTS).join(', ')}.` })
  const [start, end] = period(f)
  const plants = plantsFor(f, user)
  const rows = ledger(plants.map((p) => p.plant_id), start, end, true)
  const parents = new Map(rows.map((r) => [r.transaction_id, r]))
  for (const r of rows) {
    if (r.is_reversal && r.parent_transaction_id && !parents.has(r.parent_transaction_id)) {
      const p = ledgerRow(r.parent_transaction_id)
      if (p) parents.set(p.transaction_id, p)
    }
  }
  const orderIds = new Set(rows.map((r) => r.order_id).filter(Boolean))
  const qcByBol = new Map<string, Row>(), qcLatest = new Map<number, Row>()
  for (const q of [...store.qc].sort((a, b) => String(a.test_date).localeCompare(String(b.test_date)))) {
    if (q.active === 0 || !orderIds.has(q.order_id)) continue
    qcByBol.set(`${q.order_id}|${q.bol_number ?? ''}`, q)
    qcLatest.set(q.order_id, q)
  }
  const readings = new Map<number, Row>()
  for (const x of store.txn_reading) readings.set(x.transaction_id, { ...(readings.get(x.transaction_id) ?? {}), [x.analyte]: x.value })
  const out = rows.map((r) => {
    const name = LEGACY_TYPE_NAMES[r.type_code] ?? r.type_name ?? r.type_code
    let side: Row = r
    let sign = 1
    if (r.is_reversal) {
      const parent = parents.get(r.parent_transaction_id)
      if (parent && r.from_material_id === parent.to_material_id && r.from_location_id === parent.to_location_id && r.from_qty >= 0) {
        side = { ...parent, from_qty: r.to_qty, to_qty: r.from_qty }
        sign = -1
      } else if (parent) {
        side = { ...r, from_qty: Math.abs(r.from_qty), to_qty: Math.abs(r.to_qty) }
        sign = -1
      }
    }
    const fromQty = side.from_material_id ? -side.from_qty * sign : 0
    const toQty = side.to_material_id ? side.to_qty * sign : 0
    const loadSide = r.kind === 'LOAD' || r.kind === 'SHIP'
    const qc: Row = qcByBol.get(`${r.order_id}|${side.to_bol ?? ''}`) ?? (loadSide ? qcLatest.get(r.order_id) : undefined) ?? {}
    const seen = readings.get(r.transaction_id) ?? {}
    const pick = (a: unknown, b: unknown) => (a !== null && a !== undefined ? a : b ?? null)
    const plant = r.plant_code
    const round2 = (x: number) => Math.round(x * 100) / 100
    const rec: Row = {
      Transaction_Date: stamp(r.transaction_date, layout === 'yields'), User_Date: stamp(r.user_date, true),
      Full_TransType_Name: r.is_reversal ? `${name} - REVERSAL` : name, Order_Id: r.order_id ?? null,
      From_Material_Number: num(side.from_number), From_Material_Description: side.from_description,
      From_Location_Plant_Code: side.from_location_id ? plant : null, From_Location_Number: legacyLocation(side.from_location, plant),
      From_BOL_Number: side.from_bol || null, From_Qty: round2(fromQty),
      To_Material_Number: num(side.to_number), To_Material_Description: side.to_description,
      To_Location_Plant_Code: side.to_location_id ? plant : null, To_Location_Number: legacyLocation(side.to_location, plant),
      To_BOL_Number: side.to_bol || null, To_QC_Test_Date: stamp(qc.test_date, true) || null,
      To_QC_Moisture: pick(qc.moisture, seen.moisture), To_QC_Temp: qc.temp ?? null, To_QC_Ph: pick(qc.ph, seen.ph),
      To_QC_Spintest_Fallout: pick(qc.spintest_fallout, seen.spintest), To_QC_Sample_Number: qc.sample_number || null,
      To_Qty: round2(toQty), User_Name: r.user_name, Transaction_Remarks: r.remarks || null,
      Order_Reference: r.order_reference || null, Ship_Method: r.ship_method || null,
      GP_Vendor_ID: r.gp_vendorid, GP_Vendor_Name: r.vendor_name,
      'Trans Id': r.transaction_id, Parent: r.parent_transaction_id ?? null, 'Trans Date': stamp(r.transaction_date),
      'User Date': stamp(r.user_date, true), Type: r.is_reversal ? 'REVERSAL' : name, Ord: r.order_id ?? null, Plant: plant,
      'From Mat': num(side.from_number), 'From Mat Desc': side.from_description,
      'From Loc': legacyLocation(side.from_location, plant) ?? 0, 'From BOL': side.from_bol || null, 'From Qty': round2(fromQty),
      'To Mat': num(side.to_number), 'To Mat Desc': side.to_description, 'To Loc': legacyLocation(side.to_location, plant),
      'To BOL': side.to_bol || null, 'To Qty': round2(toQty), User: r.user_name, Dept: r.department_code,
      'From Loc Hr': r.tank_hours || 0, 'Emp Hr': r.employee_hours || 0, Seal: qc.seal_number || null,
      Remarks: r.remarks || null, Comments: r.trailer_number ? `Trailer: ${r.trailer_number}` : null,
    }
    return rec
  })
  const columns = LAYOUTS[layout].columns
  return {
    report: 'export', layout, label: LAYOUTS[layout].label, start, end, plants: plants.map((p) => p.code),
    columns, rows: out.map((rec) => Object.fromEntries(columns.map((c) => [c, rec[c] ?? null]))),
  }
}

const REPORTS: Record<string, (f: Row, user: Row) => Row> = {
  'acid-yields': acidYields, caustic, reversals, export: exportLayout,
}

export function runReport(name: string, filters: Row, user: Row): Row {
  const report = REPORTS[name]
  if (!report) invalid('Unknown report.', { report: `One of ${Object.keys(REPORTS).join(', ')}.` })
  return report(filters ?? {}, user)
}

/** The table a report is mostly about, as the spreadsheet would hold it. */
export function reportTable(result: Row): { rows: Row[]; columns: string[] } {
  if (result.report === 'export') return { rows: result.rows, columns: result.columns }
  if (result.report === 'acid-yields') return { rows: result.daily, columns: ['date', 'soap_processed', 'acid_used', 'oil_fp', 'oil_final', 'fpy_7d', 'oy_7d'] }
  if (result.report === 'caustic') {
    return {
      rows: result.loads.map((o: Row) => ({ ...o, ph_readings: o.ph_readings.join('; ') })),
      columns: ['transaction_date', 'plant', 'product', 'order_id', 'order_reference', 'ph_readings', 'gross', 'reversed', 'net', 'status'],
    }
  }
  return { rows: result.by_type, columns: ['plant', 'type', 'postings', 'reversed', 'rate_pct', 'lbs_reversed'] }
}
