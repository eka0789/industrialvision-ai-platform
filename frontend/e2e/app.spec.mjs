import AxeBuilder from '@axe-core/playwright'
import { chromium } from '@playwright/test'
import { writeFile } from 'node:fs/promises'

const baseURL = 'http://127.0.0.1:5173'
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' })
const page = await context.newPage()
const report = []
try {
  await page.goto(`${baseURL}/login`, { waitUntil: 'networkidle' })
  await page.getByLabel('Username', { exact: true }).fill('ui_operator')
  await page.getByLabel('Password', { exact: true }).fill('isolated-ui-password-2026')
  await page.getByRole('button', { name: 'Masuk ke ruang kendali' }).click()
  await page.waitForURL(`${baseURL}/`)
  for (const route of ['/', '/inspections', '/inspections/new', '/cameras', '/models', '/admin']) {
    await page.goto(`${baseURL}${route}`, { waitUntil: 'networkidle' })
    const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
    report.push({ route, violations: results.violations.map(({ id, impact, description, nodes }) => ({ id, impact, description, nodes: nodes.map(({ target, failureSummary }) => ({ target, failureSummary })) })) })
  }
  await page.goto(`${baseURL}/inspections`, { waitUntil: 'networkidle' })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(300)
  const overflow = await page.evaluate(() => ({ viewport: window.innerWidth, document: document.documentElement.scrollWidth, mobileMatches: matchMedia('(max-width: 780px)').matches, bodyMargin: getComputedStyle(document.querySelector('.app-body')).marginLeft, sidebarTransform: getComputedStyle(document.querySelector('.sidebar')).transform, sidebarClass: document.querySelector('.sidebar').className, mainWidth: document.querySelector('.main-content').getBoundingClientRect().width, elements: [...document.querySelectorAll('body *')].map((element) => ({ tag: element.tagName, class: typeof element.className === 'string' ? element.className : '', right: element.getBoundingClientRect().right, left: element.getBoundingClientRect().left, width: element.getBoundingClientRect().width })).filter((element) => element.right > window.innerWidth + 0.5 && element.left >= 0).slice(0, 20) }))
  const cssMatches = await page.evaluate(() => [...document.styleSheets].flatMap((sheet) => [...sheet.cssRules].filter((rule) => rule.cssText.includes('.app-body') || rule.cssText.includes('780px')).map((rule) => rule.cssText.slice(0, 800))))
  report.push({ mobileOverflow: overflow, cssMatches })
  await page.screenshot({ path: '.tmp/ui-audit/mobile-history-audit.png', fullPage: true })
  const mobileA11y = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  report.push({ mobileViolations: mobileA11y.violations.map(({ id, impact, nodes }) => ({ id, impact, nodes: nodes.map(({ target, failureSummary }) => ({ target, failureSummary })) })) })
  const hasFailures = report.some((entry) => entry.violations?.length || entry.mobileViolations?.length || entry.mobileOverflow?.document > entry.mobileOverflow?.viewport)
  await writeFile('.tmp/ui-audit/accessibility-report.json', JSON.stringify(report, null, 2))
  if (hasFailures) process.exitCode = 1
  await writeFile('.tmp/ui-audit/accessibility-report.json', JSON.stringify(report, null, 2))
  console.log(JSON.stringify(report, null, 2))
} finally {
  await browser.close()
}
