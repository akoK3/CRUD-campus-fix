import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'

const source = readFileSync(new URL('../src/lib/ticketPolling.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
})
const flush = () => new Promise((resolve) => setImmediate(resolve))

function setup() {
  let now = 0
  let id = 0
  const timers = new Map()
  const exports = {}
  runInNewContext(outputText, {
    exports, AbortController,
    setTimeout(fn, delay) { timers.set(++id, { fn, due: now + delay }); return id },
    clearTimeout(timer) { timers.delete(timer) },
  })
  return {
    start: exports.startPendingTicketPolling,
    async advance(ms) {
      const until = now + ms
      while (true) {
        const next = [...timers].sort((a, b) => a[1].due - b[1].due)[0]
        if (!next || next[1].due > until) break
        timers.delete(next[0]); now = next[1].due; next[1].fn()
        await flush()
      }
      now = until
      await flush()
    },
    timers,
  }
}

for (const status of ['AUTO_ROUTED', 'NEEDS_REVIEW', 'FAILED']) {
  test(`pending polling stops when routing becomes ${status}`, async () => {
    const clock = setup()
    const updates = []
    let reads = 0
    clock.start({
      read: async () => ({ ai_routing_status: ++reads === 1 ? 'PENDING' : status }),
      onUpdate: (ticket) => updates.push(ticket.ai_routing_status),
    })
    await clock.advance(6000)
    assert.deepEqual(updates, ['PENDING', status])
    assert.equal(clock.timers.size, 0)
    await clock.advance(60_000)
    assert.equal(reads, 2)
  })
}

test('a ticket that stays pending has a hard deadline and no infinite polling', async () => {
  const clock = setup()
  let reads = 0
  let expired = 0
  let signal
  clock.start({
    read: async (s) => { signal = s; reads += 1; return { ai_routing_status: 'PENDING' } },
    onUpdate() {}, onExpire: () => { expired += 1 }, maxDurationMs: 10_000,
  })
  await clock.advance(100_000)
  assert.equal(reads, 3)
  assert.equal(expired, 1)
  assert.equal(signal.aborted, true)
  assert.equal(clock.timers.size, 0)
})

test('unmount cancels in-flight reads and ignores their late results', async () => {
  const clock = setup()
  let finish
  let signal
  let updates = 0
  const stop = clock.start({
    read: (s) => { signal = s; return new Promise((resolve) => { finish = resolve }) },
    onUpdate: () => { updates += 1 },
  })
  await clock.advance(3000)
  stop()
  finish({ ai_routing_status: 'AUTO_ROUTED' })
  await flush()
  assert.equal(signal.aborted, true)
  assert.equal(updates, 0)
  assert.equal(clock.timers.size, 0)
})

test('reads never overlap and transient failures remain non-fatal', async () => {
  const clock = setup()
  let reads = 0
  let finish
  clock.start({
    read: async () => {
      reads += 1
      if (reads === 1) return new Promise((resolve) => { finish = resolve })
      if (reads === 2) throw new Error('Network unavailable')
      return { ai_routing_status: 'AUTO_ROUTED' }
    },
    onUpdate() {},
  })
  await clock.advance(12_000)
  assert.equal(reads, 1)
  finish({ ai_routing_status: 'PENDING' })
  await flush()
  await clock.advance(6000)
  assert.equal(reads, 3)
  assert.equal(clock.timers.size, 0)
})

test('missing or inaccessible tickets stop polling', async () => {
  const clock = setup()
  let reads = 0
  clock.start({ read: async () => { reads += 1; return null }, onUpdate() {} })
  await clock.advance(60_000)
  assert.equal(reads, 1)
  assert.equal(clock.timers.size, 0)
})
