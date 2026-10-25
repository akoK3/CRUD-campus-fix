import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ts from 'typescript'

const source = readFileSync(new URL('../src/components/TicketAiDetails.tsx', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX },
})
const exports = {}
runInNewContext(outputText, { exports, require: createRequire(import.meta.url) })
const ticket = {
  title: 'Clogged bathroom sink', description: 'The bathroom sink drains slowly.',
  original_title: '  Bathroom sink clogged  ', original_text: '  my dorm sink drains slow!!!  ',
  ai_explanation: 'A blocked sink requires a plumbing repair.',
}
const render = (data = ticket, isStaff = false) => renderToStaticMarkup(
  React.createElement(exports.TicketAiDetails, { ticket: data, isStaff }),
)

test('the stored concise explanation is visible and original details stay hidden for students', () => {
  const html = render()
  assert.match(html, /AI routing explanation/)
  assert.match(html, /A blocked sink requires a plumbing repair/)
  assert.doesNotMatch(html, /Original student submission/)
})

test('staff can expand the verbatim original without mutating it', () => {
  const before = JSON.stringify(ticket)
  const html = render(ticket, true)
  assert.match(html, /<details/)
  assert.match(html, /Original student submission/)
  assert.match(html, /  Bathroom sink clogged  /)
  assert.match(html, /  my dorm sink drains slow!!!  /)
  assert.equal(JSON.stringify(ticket), before)
})

test('empty explanations and identical originals do not add empty UI sections', () => {
  const html = render({ ...ticket, ai_explanation: null,
    original_title: ticket.title, original_text: ticket.description }, true)
  assert.doesNotMatch(html, /AI routing explanation|Original student submission/)
})

test('legacy null titles are not invented and stored text is escaped', () => {
  const html = render({ ...ticket, original_title: null, ai_explanation: '<script>bad()</script>' }, true)
  assert.match(html, /original title was not recorded/)
  assert.doesNotMatch(html, /<script>/)
  assert.match(html, /&lt;script&gt;/)
})
