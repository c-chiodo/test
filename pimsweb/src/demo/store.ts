/* In-memory store for the browser-only demo build.
 *
 * The real system keeps this data in SQL and applies the rules in
 * `pims/services/`. The demo build has no server, so the seeded dataset is
 * loaded here and the rules are re-implemented alongside it in `api.ts`.
 * Everything lives in memory: edits are real for the session and a reload
 * puts the sandbox back to its starting state. */

import dataset from './dataset.json'

export type Row = Record<string, any>

export interface Store {
  company: Row[]
  plant: Row[]
  department: Row[]
  order_type: Row[]
  status: Row[]
  material_type: Row[]
  material: Row[]
  material_plant: Row[]
  material_test: Row[]
  material_spec: Row[]
  blend_recipe: Row[]
  blend_recipe_component: Row[]
  location_type: Row[]
  location: Row[]
  location_default: Row[]
  customer: Row[]
  vendor: Row[]
  partner_requirement: Row[]
  transaction_type: Row[]
  order: Row[]
  inventory_transaction: Row[]
  pending_shipment: Row[]
  qc: Row[]
  test_point: Row[]
  qc_in_process: Row[]
  qa_question: Row[]
  qa_header: Row[]
  qa_response: Row[]
  lims_result: Row[]
  saved_query: Row[]
  system_setting: Row[]
  app_user: Row[]
  user_plant_access: Row[]
  audit_log: Row[]
  // Automation state the sandbox keeps in memory alongside the seeded data.
  number_sequence: Row[]
  alert_log: Row[]
  recurring_order: Row[]
  scale_reading: Row[]
  job_run: Row[]
}

export const store: Store = {
  ...(dataset as unknown as Store),
  audit_log: [],
  // Counters start above the seeded series so a generated number never
  // collides with a historical one, exactly as the server seeds them.
  number_sequence: [
    { key: 'bol', next_value: 112_000 },
    { key: 'sample', next_value: 400_000 },
  ],
  alert_log: [],
  recurring_order: [],
  scale_reading: [],
  job_run: [],
}

/** Next id for a table, so created rows keep climbing like the real sequences. */
export function nextId(table: keyof Store, key: string, floor = 0): number {
  const rows = store[table] as Row[]
  const highest = rows.reduce((max, row) => Math.max(max, Number(row[key]) || 0), floor)
  return highest + 1
}

export function index<T extends Row>(rows: T[], key: string): Map<any, T> {
  const map = new Map<any, T>()
  for (const row of rows) map.set(row[key], row)
  return map
}

export const byId = {
  material: () => index(store.material, 'material_id'),
  location: () => index(store.location, 'location_id'),
  plant: () => index(store.plant, 'plant_id'),
  customer: () => index(store.customer, 'customer_id'),
  vendor: () => index(store.vendor, 'vendor_id'),
  department: () => index(store.department, 'department_id'),
  status: () => index(store.status, 'status_id'),
  orderType: () => index(store.order_type, 'order_type_id'),
  company: () => index(store.company, 'company_id'),
  user: () => index(store.app_user, 'user_id'),
  order: () => index(store.order, 'order_id'),
  transactionType: () => index(store.transaction_type, 'transaction_type_id'),
  testPoint: () => index(store.test_point, 'test_point_id'),
  question: () => index(store.qa_question, 'question_id'),
}

export function nowIso(): string {
  return new Date().toISOString().replace(/\.\d+Z$/, '+00:00')
}

export function todayIso(): string {
  return new Date().toISOString().slice(0, 10)
}

export function hoursSince(value: string | null | undefined): number | null {
  if (!value) return null
  const then = new Date(String(value).replace(' ', 'T')).getTime()
  if (Number.isNaN(then)) return null
  return (Date.now() - then) / 3_600_000
}

/** The demo build has no LIMS job, so the projection is stamped as fresh on
 *  load. Otherwise every sandbox visit would open on a failed health check. */
export function refreshLimsStamps(): void {
  const stamp = nowIso()
  for (const row of store.lims_result) row.retrieved_at = stamp
}

refreshLimsStamps()
