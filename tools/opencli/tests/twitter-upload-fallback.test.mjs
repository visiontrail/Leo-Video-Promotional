import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { getRegistry } from '@jackwener/opencli/registry'
import { isRecoverableFileInputError } from '../node_modules/@jackwener/opencli/clis/twitter/utils.js'
import '../node_modules/@jackwener/opencli/clis/twitter/post.js'

test('falls back when the browser bridge receives no file chooser event', () => {
  assert.equal(
    isRecoverableFileInputError(
      new Error(
        'Page.fileChooserOpened not received within 5s — the input may not have opened a file chooser',
      ),
    ),
    true,
  )
})

test('does not hide unrelated browser permission failures', () => {
  assert.equal(isRecoverableFileInputError(new Error('Permission denied')), false)
})

test('twitter post uses the DataTransfer fallback after the chooser timeout', async (t) => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'opencli-twitter-fallback-'))
  const imagePath = path.join(tempDir, 'image.jpg')
  fs.writeFileSync(imagePath, Buffer.from([0xff, 0xd8, 0xff, 0xd9]))
  t.after(() => fs.rmSync(tempDir, { recursive: true, force: true }))

  const evaluateResults = [
    { ok: true },
    { ok: true, previewCount: 1 },
    { ok: true },
    { ok: true },
    { ok: true },
    { ok: true, message: 'Tweet posted successfully.' },
  ]
  const evaluatedScripts = []
  const page = {
    goto: async () => {},
    wait: async () => {},
    setFileInput: async () => {
      throw new Error(
        'Page.fileChooserOpened not received within 5s — the input may not have opened a file chooser',
      )
    },
    insertText: async () => {},
    evaluate: async (script) => {
      evaluatedScripts.push(script)
      return evaluateResults.shift()
    },
  }

  const command = getRegistry().get('twitter/post')
  const result = await command.func(page, { text: 'fallback proof', images: imagePath })

  assert.equal(result[0].status, 'success')
  assert.equal(result[0].text, 'fallback proof')
  const fallbackScript = evaluatedScripts.find((script) => script.includes('new DataTransfer()'))
  assert.ok(fallbackScript)
  assert.match(fallbackScript, /closest\('\[role="dialog"\]'\)/)
  assert.match(fallbackScript, /startsWith\('__reactProps\$'\)/)
  assert.match(fallbackScript, /reactOnChange\(\{/)
  assert.equal(evaluateResults.length, 0)
})
