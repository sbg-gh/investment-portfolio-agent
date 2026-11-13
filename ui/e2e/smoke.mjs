// Browser smoke test: drives the REAL server (offline scripted model, temp database, synthetic users)
// with the locally installed Google Chrome through playwright-core. Optional: the unit suites do not
// need it. Exit codes: 0 = passed, 1 = a check failed, 2 = could not run (no Chrome / server did not
// start / ui not built). Never reports a pass for a step that did not execute.
//
// Needs: `npm --prefix ui run build` done, the ui extra installed in the Python named by E2E_PYTHON
// (default python3.11), and Google Chrome installed.
import { spawn } from 'node:child_process';
import { strict as assert } from 'node:assert';
import { Buffer } from 'node:buffer';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { chromium } from 'playwright-core';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..', '..');
const python = process.env.E2E_PYTHON ?? 'python3.11';
const done = [];

function cannotRun(reason) {
  console.log(`E2E COULD NOT RUN: ${reason}`);
  process.exit(2);
}

function step(name) {
  done.push(name);
  console.log(`step ${done.length}: ${name}`);
}

function startServer() {
  if (!existsSync(path.join(root, 'src', 'portfolio_agent', 'web', 'static', 'index.html'))) {
    cannotRun('the UI is not built; run `npm --prefix ui run build` first');
  }
  const child = spawn(python, [path.join(here, 'serve_for_e2e.py')], {
    cwd: root,
    env: { ...process.env, PYTHONPATH: path.join(root, 'src') },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  return new Promise((resolve) => {
    let out = '';
    let err = '';
    child.stderr.on('data', (d) => { err += d; });
    child.stdout.on('data', (d) => {
      out += d;
      const line = out.split('\n')[0];
      if (out.includes('\n')) resolve({ child, info: JSON.parse(line) });
    });
    child.on('exit', (code) => {
      if (!out.includes('\n')) cannotRun(`the server exited with code ${code} before it started: ${err.trim().split('\n').pop()}`);
    });
  });
}

function utcMinute(offsetMinutes) {
  return new Date(Date.now() + offsetMinutes * 60000).toISOString().slice(0, 16);
}

async function signIn(page, url, token) {
  await page.goto(url);
  await page.getByLabel('Access token').fill(token);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.getByRole('navigation', { name: 'Primary' }).waitFor();
}

async function register(page, id, symbol) {
  await page.locator('#reg-id').fill(id);
  await page.locator('#reg-symbol').fill(symbol);
  await page.getByRole('button', { name: 'Register instrument' }).click();
  await page.getByText(`Registered ${id}.`).waitFor();
}

async function observe(page, id, value, observedAt) {
  await page.locator('#obs-id').fill(id);
  await page.locator('#obs-value').fill(value);
  await page.locator('#obs-at').fill(observedAt);
  await page.getByRole('button', { name: 'Record observation' }).click();
  await page.getByText('Observation recorded.').waitFor();
}

async function importHolding(page, symbol, quantity) {
  await page.getByLabel('Symbol').fill(symbol);
  await page.getByLabel('Quantity').fill(quantity);
  await page.getByRole('button', { name: 'Import holding' }).click();
  await page.getByRole('cell', { name: symbol, exact: true }).first().waitFor();
}

function utcDate(offsetDays) {
  return new Date(Date.now() + offsetDays * 86400000).toISOString().slice(0, 10);
}
async function main() {
  const { child, info } = await startServer();
  let browser;
  try {
    try {
      browser = await chromium.launch({ channel: 'chrome', headless: true });
    } catch (error) {
      cannotRun(`Google Chrome could not be launched through playwright-core (${String(error.message).split('\n')[0]})`);
    }
    const page = await browser.newPage();
    page.setDefaultTimeout(15000);
    const failures = [];
    page.on('pageerror', (e) => failures.push(`page error: ${e.message}`));
    await signIn(page, info.url, info.tokens.operator);
    step('login with the operator token');

    await page.getByRole('link', { name: 'Operator' }).click();
    await register(page, 'op:acme', 'ACME');
    await register(page, 'op:oldco', 'OLDCO');
    step('register two instruments (operator-attested)');

    await observe(page, 'op:acme', '10', utcMinute(-5));
    await observe(page, 'op:oldco', '5', utcMinute(-10 * 24 * 60));
    step('record a fresh price and a ten-day-old price');

    await page.getByRole('link', { name: 'Portfolios' }).click();
    await page.getByLabel('Portfolio name').fill('E2E portfolio');
    await page.getByRole('button', { name: 'Create portfolio' }).click();
    await page.getByRole('link', { name: 'E2E portfolio', exact: true }).click();
    await page.getByRole('heading', { name: 'Holdings', level: 1 }).waitFor();
    await importHolding(page, 'ACME', '3');
    step('create a portfolio and import ACME x 3');

    await page.getByRole('link', { name: 'Valuation' }).click();
    await page.getByRole('button', { name: 'Value portfolio' }).click();
    const total = page.getByTestId('total');
    await total.waitFor();
    assert.match(await total.innerText(), /USD 30(\.0+)?\b/);
    assert.equal(await page.getByTestId('total-withheld').count(), 0);
    step('valuation shows the total (3 x 10 = 30) when every holding is fresh');

    await page.getByRole('link', { name: 'Back to holdings' }).click();
    await page.getByRole('heading', { name: 'Holdings', level: 1 }).waitFor();
    await importHolding(page, 'OLDCO', '2');
    await page.getByRole('link', { name: 'Valuation' }).click();
    await page.getByRole('button', { name: 'Value portfolio' }).click();
    const withheld = page.getByTestId('total-withheld');
    await withheld.waitFor();
    assert.match(await withheld.innerText(), /Total withheld: 0 missing, 1 stale/);
    assert.equal(await page.getByTestId('total').count(), 0, 'no total may be shown with a stale line');
    assert.match(await page.getByTestId('subtotal').innerText(), /Partial subtotal/);
    step('a stale price withholds the total and labels the subtotal as partial');

    await page.getByRole('link', { name: 'Operator' }).click();
    await page.getByLabel('Price list name').fill('e2e-csv');
    const day = utcDate(-2);
    const csv = `symbol,date,close,volume\nACME,${day},12.50,1\nOLDCO,${day},6.00,1\nNOPE,${day},1,1\n`;
    await page.getByLabel('CSV file').setInputFiles({ name: 'synthetic.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) });
    assert.equal(await page.getByRole('button', { name: 'Import', exact: true }).isDisabled(), true, 'Import needs a validation first');
    await page.getByRole('button', { name: 'Validate' }).click();
    await page.getByRole('table', { name: 'Per-row results' }).waitFor();
    await page.getByRole('cell', { name: 'Symbol not registered' }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Import', exact: true }).isDisabled(), false);
    step('CSV validate shows per-row results (one unregistered symbol) and enables Import');
    await page.getByRole('button', { name: 'Import', exact: true }).click();
    await page.getByText(/Import finished into price list e2e-csv/).waitFor();
    step('CSV import of two synthetic prices (one row rejected)');
    await page.getByRole('link', { name: 'Portfolios' }).click();
    await page.getByRole('link', { name: 'E2E portfolio', exact: true }).click();
    await page.getByRole('link', { name: 'Valuation' }).click();
    await page.locator('#source').fill('e2e-csv');
    await page.getByRole('button', { name: 'Value portfolio' }).click();
    await page.getByTestId('total-withheld').waitFor();
    assert.match(await page.getByTestId('window').innerText(), /Prices up to 1 day old/);
    await page.getByLabel('Accept prices up to').selectOption('3');
    await page.getByRole('button', { name: 'Value portfolio' }).click();
    await page.getByTestId('total').waitFor();
    assert.match(await page.getByTestId('total').innerText(), /USD 49\.5/);
    assert.match(await page.getByTestId('window').innerText(), /Prices up to 3 days old/);
    step('imported prices are stale under the 1-day window (total withheld) and complete under 3 days (3 x 12.5 + 2 x 6 = 49.5)');
    await page.getByRole('link', { name: 'Operator' }).click();
    await page.getByRole('button', { name: 'Fetch prices from Yahoo (unofficial)' }).click();
    const fetchTable = page.getByRole('table', { name: 'Per-symbol results' });
    await fetchTable.waitFor();
    assert.equal(await page.getByRole('cell', { name: '123.4567' }).count(), 2);
    assert.match(await page.getByRole('status').filter({ hasText: 'up to date' }).innerText(), /All 2 symbols are up to date/);
    step('Yahoo fetch button against the offline fake transport (never reaches Yahoo): two symbols added with their close dates');
    await page.getByRole('link', { name: 'Portfolios' }).click();
    await page.getByRole('link', { name: 'E2E portfolio', exact: true }).click();
    await page.getByRole('link', { name: 'Valuation' }).click();
    await page.locator('#source').fill('yahoo-chart');
    await page.getByLabel('Accept prices up to').selectOption('5');
    await page.getByRole('button', { name: 'Value portfolio' }).click();
    await page.getByTestId('total').waitFor();
    assert.match(await page.getByTestId('total').innerText(), /USD 617\.2835/);
    step('the yahoo-chart source values the portfolio under a 5-day window (5 x 123.4567 = 617.2835)');
    await page.getByRole('link', { name: 'Chat' }).click();
    await page.getByLabel('Portfolio (optional)').selectOption({ label: 'E2E portfolio' });
    await page.getByLabel('Your message').fill('What do I hold?');
    await page.getByRole('button', { name: 'Send' }).click();
    await page.getByText('E2E scripted reply').waitFor();
    await page.getByRole('table', { name: 'Evidence' }).waitFor();
    step('chat round trip with the scripted offline model, evidence table shown');

    await page.getByRole('button', { name: 'Sign out' }).click();
    await page.getByRole('button', { name: 'Sign in' }).waitFor();
    await page.goBack();
    await page.waitForTimeout(300);
    assert.equal(await page.getByRole('navigation', { name: 'Primary' }).count(), 0, 'no private navigation after logout');
    assert.equal(await page.getByText('E2E portfolio').count(), 0, 'no private data after logout and back');
    const status = await page.evaluate(async () => (await fetch('/api/portfolios', { credentials: 'same-origin' })).status);
    assert.equal(status, 401);
    step('after logout the back button shows the sign-in form and the API answers 401');

    assert.deepEqual(failures, [], 'the browser reported page errors');
    console.log(`E2E PASSED: ${done.length} steps in Chrome ${browser.version()} against a real server (offline scripted model).`);
  } catch (error) {
    console.log(`E2E FAILED after ${done.length} completed steps: ${error.message}`);
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    child.kill('SIGTERM');
  }
}

await main();
