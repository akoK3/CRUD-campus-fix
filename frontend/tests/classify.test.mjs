import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'

// Exercise the real TypeScript helper with browser/Supabase boundaries mocked.
// Replace Vite's import.meta with a supplied environment; no live API or keys.
const source = readFileSync(new URL('../src/lib/classify.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  transformers: {
    before: [(context) => {
      const visit = (node) => ts.isMetaProperty(node)
        ? ts.factory.createIdentifier('importMeta')
        : ts.visitEachChild(node, visit, context)
      return (node) => ts.visitNode(node, visit)
    }],
  },
})

function loadClassifier({
  apiUrl = 'https://api.example.com',
  getSession = async () => ({ data: { session: { access_token: 'test-user-token' } } }),
  fetch = async () => ({ status: 200 }),
} = {}) {
  let now = 0
  let timerId = 0
  const timers = new Map()
  const exports = {}
  runInNewContext(outputText, {
    exports,
    importMeta: { env: { VITE_API_URL: apiUrl } },
    require(name) {
      assert.equal(name, './supabase')
      return { supabase: { auth: { getSession } } }
    },
    fetch,
    AbortController,
    setTimeout(callback, delay) {
      timers.set(++timerId, { callback, due: now + delay })
      return timerId
    },
    clearTimeout(id) { timers.delete(id) },
  })
  return {
    classifyTicket: exports.classifyTicket,
    onClassificationFinished: exports.onClassificationFinished,
    advanceTime(milliseconds) {
      now += milliseconds
      for (const [id, timer] of timers) {
        if (timer.due <= now) {
          timers.delete(id)
          timer.callback()
        }
      }
    },
  }
}

test('a long cold start does not abort the authenticated classification request', { timeout: 2000 }, async () => {
  let request
  let finishRequest
  let notifyStarted
  const started = new Promise((resolve) => { notifyStarted = resolve })
  const { classifyTicket, advanceTime } = loadClassifier({
    fetch(url, options) {
      request = { url, options }
      notifyStarted()
      return new Promise((resolve, reject) => {
        finishRequest = resolve
        options.signal?.addEventListener('abort', () => reject(new Error('Aborted')))
      })
    },
  })
  let settled = false
  const classification = classifyTicket(42).finally(() => { settled = true })
  await started

  assert.equal(request.url, 'https://api.example.com/api/tickets/42/classify')
  assert.equal(request.options.method, 'POST')
  assert.equal(request.options.headers.Authorization, 'Bearer test-user-token')
  assert.equal(request.options.headers['Content-Type'], 'application/json')

  // No real wait: advance far beyond the old 8s limit and a warm ~19s call.
  advanceTime(240_000)
  await Promise.resolve()
  assert.equal(request.options.signal?.aborted ?? false, false)
  assert.equal(settled, false)

  finishRequest({ status: 200 })
  await classification
  assert.equal(settled, true)
})

test('no configured API leaves ticket creation independent of AI', async () => {
  let sessionLookups = 0
  let requests = 0
  const { classifyTicket } = loadClassifier({
    apiUrl: '',
    getSession: async () => {
      sessionLookups += 1
      return { data: { session: null } }
    },
    fetch: async () => { requests += 1 },
  })
  await assert.doesNotReject(classifyTicket(42))
  assert.equal(sessionLookups, 0)
  assert.equal(requests, 0)
})

test('a missing session never sends an unauthenticated request', async () => {
  let requests = 0
  const { classifyTicket } = loadClassifier({
    getSession: async () => ({ data: { session: null } }),
    fetch: async () => { requests += 1 },
  })
  await assert.doesNotReject(classifyTicket(42))
  assert.equal(requests, 0)
})

test('a session lookup failure cannot fail a saved ticket', async () => {
  let requests = 0
  const { classifyTicket } = loadClassifier({
    getSession: async () => { throw new Error('Session unavailable') },
    fetch: async () => { requests += 1 },
  })
  await assert.doesNotReject(classifyTicket(42))
  assert.equal(requests, 0)
})

test('a network failure cannot reject the submission follow-up', async () => {
  let requests = 0
  const { classifyTicket } = loadClassifier({
    fetch: async () => {
      requests += 1
      throw new TypeError('Failed to fetch')
    },
  })
  await assert.doesNotReject(classifyTicket(42))
  assert.equal(requests, 1)
})

test('a proxy timeout response cannot reject the submission follow-up', async () => {
  let requests = 0
  const { classifyTicket } = loadClassifier({
    fetch: async () => {
      requests += 1
      return { ok: false, status: 524 }
    },
  })
  await assert.doesNotReject(classifyTicket(42))
  assert.equal(requests, 1)
})


test('completion observers run after the full POST, including enrichment', { timeout: 2000 }, async () => {
  let finish
  let notifyStarted
  const started = new Promise((resolve) => { notifyStarted = resolve })
  const { classifyTicket, onClassificationFinished } = loadClassifier({
    fetch: () => new Promise((resolve) => { finish = resolve; notifyStarted() }),
  })
  const events = []
  const unsubscribe = onClassificationFinished((id) => events.push(id))
  const request = classifyTicket(42)
  await started
  assert.deepEqual(events, [])
  finish({ status: 200 })
  await request
  assert.deepEqual(events, [42])
  unsubscribe()
})

test('completion listeners unsubscribe and cannot cause a false submission failure', async () => {
  const { classifyTicket, onClassificationFinished } = loadClassifier()
  let calls = 0
  const unsubscribe = onClassificationFinished(() => { calls += 1 })
  onClassificationFinished(() => { throw new Error('UI failure') })
  await assert.doesNotReject(classifyTicket(42))
  assert.equal(calls, 1)
  unsubscribe()
  await assert.doesNotReject(classifyTicket(42))
  assert.equal(calls, 1)
})
