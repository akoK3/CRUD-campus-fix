import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'

const source = readFileSync(new URL('../src/lib/submission.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
})
const exports = {}
runInNewContext(outputText, { exports })
const { submissionText } = exports

test('ticket creation captures the exact title and description before AI runs', () => {
  const title = '  Sink broken!!!  '
  const description = '  Water drips.\nPlease help.  '
  const fields = submissionText(title, description)
  assert.equal(fields.original_title, title)
  assert.equal(fields.original_text, description)
  assert.equal(fields.title, 'Sink broken!!!')
  assert.equal(fields.description, 'Water drips.\nPlease help.')

  // Subsequent display enrichment must not alter the captured submission.
  Object.assign(fields, { title: 'Leaking sink', description: 'The sink drips.' })
  assert.equal(fields.original_title, title)
  assert.equal(fields.original_text, description)
})

test('submission capture preserves punctuation and Unicode without normalizing originals', () => {
  const title = '  Caf\u00e9 door: won\u2019t open?  '
  const description = '\tRoom 12\r\nAccessibility issue.\n'
  const fields = submissionText(title, description)
  assert.equal(fields.original_title, title)
  assert.equal(fields.original_text, description)
  assert.equal(fields.title, title.trim())
  assert.equal(fields.description, description.trim())
})
