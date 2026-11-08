// Tests for how the agent's look-ups are shown (#158). Run with `node --test` in this folder.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { lookupCount, stepWindow } from '../src/agent/stepsLogic.js'

const local = (year, month, date, hours, minutes) => new Date(year, month - 1, date, hours, minutes)
const iso = (date) => date.toISOString()
const NOW = local(2026, 11, 7, 12, 0).getTime()

test('a window within one day shows the day once', () => {
  assert.equal(stepWindow({ from: iso(local(2026, 11, 3, 10, 0)), to: iso(local(2026, 11, 3, 12, 0)) }, NOW), '3 Nov 10:00–12:00')
})

test('a window over several days shows both days', () => {
  assert.equal(stepWindow({ from: iso(local(2026, 10, 30, 8, 5)), to: iso(local(2026, 11, 2, 9, 0)) }, NOW), '30 Oct 08:05 – 2 Nov 09:00')
})

test('a window that ends about now ends "now"', () => {
  assert.equal(stepWindow({ from: iso(local(2026, 11, 6, 12, 0)), to: iso(local(2026, 11, 7, 11, 59)) }, NOW), '6 Nov 12:00 – now')
})

test('one moment is shown with "at", or as now', () => {
  assert.equal(stepWindow({ at: iso(local(2026, 11, 5, 9, 30)) }, NOW), 'at 5 Nov 09:30')
  assert.equal(stepWindow({ at: iso(local(2026, 11, 7, 12, 0)) }, NOW), 'now')
})

test('a time without a zone is read as UTC, as the tools read it', () => {
  const utcMidnight = new Date(Date.UTC(2026, 10, 3, 0, 0))
  assert.equal(stepWindow({ at: '2026-11-03T00:00:00' }, NOW), stepWindow({ at: utcMidnight.toISOString() }, NOW))
})

test('no window, or an unreadable one, shows nothing', () => {
  assert.equal(stepWindow({ from: null, to: null, at: null }, NOW), '')
  assert.equal(stepWindow({ from: 'soon', to: 'later' }, NOW), '')
  assert.equal(stepWindow(undefined, NOW), '')
})

test('the count leaves out calls that were refused', () => {
  assert.equal(lookupCount([{ status: 'ok' }, { status: 'error' }, { status: 'refused' }]), '2 look-ups')
  assert.equal(lookupCount([{ status: 'ok' }]), '1 look-up')
  assert.equal(lookupCount(undefined), '0 look-ups')
})
