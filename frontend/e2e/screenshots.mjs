// Screenshot generator for the README (docs/screenshots).
// Runs against the DEV demo stack: backend on :8001 (seeded), vite on :5173.
import { chromium } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

const API = 'http://127.0.0.1:8001'
const APP = 'http://localhost:5173'
const OUT = '../docs/screenshots'

const api = async (method, path, payload, token) => {
  const headers = {}
  if (token) headers.Authorization = `Bearer ${token}`
  let body
  if (payload !== undefined) {
    body = JSON.stringify(payload)
    headers['Content-Type'] = 'application/json'
  }
  const response = await fetch(`${API}${path}`, { method, headers, body })
  if (!response.ok && response.status !== 409) {
    throw new Error(`${method} ${path} -> ${response.status}: ${await response.text()}`)
  }
  return response.json().catch(() => ({}))
}

const login = async (username, password) =>
  (await api('POST', '/auth/login', { username, password })).token.access_token

const main = async () => {
  await mkdir(OUT, { recursive: true })

  // ── Setup: richer demo data for the new modules ─────────────────────────
  const admin = await login('admin', 'Admin!Vision2026')
  const operator = await login('operator1', 'Operator!2026')

  // Machines + readings + one prediction-worthy machine (PdM).
  const machineDefs = [
    { name: 'CNC Mill 01', code: 'CNC-01', machine_type: 'cnc', readings: [300, 306.5, 1320, 26.5, 235] },
    { name: 'Hydraulic Press 02', code: 'PRESS-02', machine_type: 'press', readings: [298, 308.5, 1510, 28.5, 120] },
    { name: 'Conveyor Belt 03', code: 'CONV-03', machine_type: 'conveyor', readings: [297, 307, 1480, 27, 60] },
  ]
  for (const def of machineDefs) {
    const created = await api('POST', '/pdm/machines', { name: def.name, code: def.code, machine_type: def.machine_type }, admin)
    let machine = created
    if (!machine.id) {
      // 409: machine already exists — look it up by code.
      const machines = await api('GET', '/pdm/machines', undefined, admin)
      machine = machines.find((m) => m.code === def.code)
    }
    for (let i = 0; i < 6; i += 1) {
      const jitter = (base, spread) => Math.round((base + (Math.random() - 0.5) * spread) * 10) / 10
      await api('POST', `/pdm/machines/${machine.id}/readings`, {
        air_temperature_k: jitter(def.readings[0], 1.5),
        process_temperature_k: jitter(def.readings[1], 1.5),
        rotational_speed_rpm: jitter(def.readings[2], 40),
        torque_nm: jitter(def.readings[3], 3),
        tool_wear_min: jitter(def.readings[4] + i * 4, 2),
      }, operator)
    }
  }

  // Pending maintenance request from the operator (admin approves later in UI).
  const machines = await api('GET', '/pdm/machines', undefined, admin)
  const press = machines.find((m) => m.code === 'PRESS-02')
  const cnc = machines.find((m) => m.code === 'CNC-01')
  try {
    await api('POST', '/pdm/requests', {
      machine_id: press.id, title: 'Ganti seal silinder hidrolik', priority: 'high',
      description: 'Terdeteksi tekanan turun saat siklus pressing.',
    }, operator)
    await api('POST', '/pdm/requests', {
      machine_id: cnc.id, title: 'Kalibrasi ulang spindle', priority: 'medium',
    }, operator)
  } catch (error) {
    console.log('request setup skipped:', error.message)
  }

  // Knowledge document (skip when already present; prune duplicates).
  try {
    const existingDocs = await api('GET', '/rag/documents', undefined, admin)
    for (const duplicate of existingDocs.slice(1)) {
      await api('DELETE', `/rag/documents/${duplicate.id}`, undefined, admin)
    }
    if (existingDocs.length === 0) {
    const boundary = '----ivshot'
    const content = [
      'Standard Operating Procedure — Quality Gate SMT Line 2',
      '',
      '1. Operators must verify the stencil tension before every shift start.',
      '2. Solder paste thickness is acceptable between 100 and 150 micrometers.',
      '3. SPI failure on three consecutive boards requires a line stop and supervisor call.',
      '4. The reflow oven profile must match the recipe card attached to the machine.',
      '5. ESD wrist straps are mandatory inside the marked yellow floor area.',
    ].join('\n')
    const body = `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="sop-smt-line2.txt"\r\nContent-Type: text/plain\r\n\r\n${content}\r\n--${boundary}--\r\n`
      const response = await fetch(`${API}/rag/documents`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${admin}`, 'Content-Type': `multipart/form-data; boundary=${boundary}` },
        body,
      })
      if (!response.ok) console.log('knowledge setup:', response.status, await response.text())
    }
  } catch (error) {
    console.log('knowledge setup skipped:', error.message)
  }

  // Agent run history + a pending approval for the inbox.
  try {
    await api('POST', '/agent/chat', { question: 'show latest inspections' }, admin)
    await api('POST', '/agent/chat', { question: 'create maintenance request for machine #1 check spindle bearing temperature' }, operator)
  } catch (error) {
    console.log('agent setup skipped:', error.message)
  }

  // ── Screenshots ──────────────────────────────────────────────────────────
  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2, // crisp text on GitHub
    reducedMotion: 'reduce',
  })
  const page = await context.newPage()

  const shot = async (name, fn) => {
    await fn()
    await page.waitForTimeout(600)
    await page.screenshot({ path: `${OUT}/${name}.png` })
    console.log('captured', name)
  }

  // 1. Login page (fresh, logged out).
  await shot('01-login', async () => {
    await page.goto(`${APP}/login`, { waitUntil: 'networkidle' })
  })

  // Login through the real form.
  await page.getByLabel('Username', { exact: true }).fill('admin')
  await page.getByLabel('Password', { exact: true }).fill('Admin!Vision2026')
  await page.getByRole('button', { name: /Masuk/i }).click()
  await page.waitForURL(`${APP}/`, { timeout: 30000 })

  // 2. Overview — give the live feed a moment to push an inspection.
  await shot('02-overview', async () => {
    await page.goto(`${APP}/`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(9000) // demo feed interval is 5s
  })

  // 3. New inspection.
  await shot('03-new-inspection', async () => {
    await page.goto(`${APP}/inspections/new`, { waitUntil: 'networkidle' })
  })

  // 4. History.
  await shot('04-history', async () => {
    await page.goto(`${APP}/inspections`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(800)
  })

  // 5. Inspection detail (first row with evidence).
  const inspections = await api('GET', '/inspections?page_size=10', undefined, admin)
  const withEvidence = inspections.items.find((item) => item.evidence_path) ?? inspections.items[0]
  await shot('05-inspection-detail', async () => {
    await page.goto(`${APP}/inspections/${withEvidence.id}`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1200) // evidence image decode
  })

  // 6. Cameras.
  await shot('06-cameras', async () => {
    await page.goto(`${APP}/cameras`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(800)
  })

  // 7. Models.
  await shot('07-models', async () => {
    await page.goto(`${APP}/models`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(800)
  })

  // 8. Maintenance + prediction modal.
  await shot('08-maintenance', async () => {
    await page.goto(`${APP}/maintenance`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(900)
  })
  await shot('09-maintenance-prediction', async () => {
    const predictButton = page.getByRole('button', { name: 'Prediksi' }).first()
    await predictButton.click()
    await page.waitForSelector('#prediction-title', { timeout: 20000 })
    await page.waitForTimeout(500)
  })

  // 10. Knowledge + answered chat with citations.
  await shot('10-knowledge', async () => {
    await page.goto(`${APP}/knowledge`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(800)
    const input = page.getByLabel('Pertanyaan')
    await input.fill('berapa ketebalan solder paste yang masih acceptable?')
    await page.getByRole('button', { name: /Tanya/i }).click()
    await page.waitForSelector('[data-testid="chat-answer"]', { timeout: 30000 })
    await page.waitForTimeout(600)
  })

  // 11. Agent workspace with a fresh run.
  await shot('11-agent', async () => {
    await page.goto(`${APP}/agent`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(800)
    await page.getByLabel('Pertanyaan untuk agen').fill('show latest inspections')
    await page.getByRole('button', { name: /Kirim/i }).click()
    await page.waitForSelector('[data-testid="agent-answer"]', { timeout: 30000 })
    await page.waitForTimeout(600)
  })

  // 12. Monitoring.
  await shot('12-monitoring', async () => {
    await page.goto(`${APP}/monitoring`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(900)
  })

  // 13. Lab with a completed simulation.
  await shot('13-lab', async () => {
    await page.goto(`${APP}/lab`, { waitUntil: 'networkidle' })
    await page.getByRole('button', { name: /Jalankan 400 episode/i }).click()
    await page.waitForSelector('[data-testid="sim-results"]', { timeout: 30000 })
    await page.waitForTimeout(500)
  })

  // 14. Admin.
  await shot('14-admin', async () => {
    await page.goto(`${APP}/admin`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(900)
  })

  // 16. Food Division — SKU Recipes & Multi-Zone ROI Editor.
  await shot('16-recipes', async () => {
    await page.goto(`${APP}/recipes`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(900)
  })

  // 17. Food Division — Production Batch & HACCP Traceability.
  await shot('17-batches', async () => {
    await page.goto(`${APP}/batches`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(900)
  })

  // 15. Mobile overview (390px responsiveness proof).
  await page.setViewportSize({ width: 390, height: 844 })
  await shot('15-mobile-overview', async () => {
    await page.goto(`${APP}/`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1500)
  })

  await browser.close()
  console.log('done')
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
