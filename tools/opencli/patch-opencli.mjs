import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const runtimeDir = path.dirname(fileURLToPath(import.meta.url))
const geminiDir = path.join(
  runtimeDir,
  'node_modules',
  '@jackwener',
  'opencli',
  'clis',
  'gemini',
)
const askPath = path.join(geminiDir, 'ask.js')
const utilsPath = path.join(geminiDir, 'utils.js')
const manifestPath = path.join(
  runtimeDir,
  'node_modules',
  '@jackwener',
  'opencli',
  'cli-manifest.json',
)

const helperMarker = 'export async function attachGeminiFile(page, filePath)'
const helperSource = fs.readFileSync(
  path.join(runtimeDir, 'patches', 'gemini-file-upload-helper.js'),
  'utf8',
)

function replaceOnce(source, before, after, label) {
  if (source.includes(after)) return source
  if (!source.includes(before)) {
    throw new Error(`OpenCLI ${label} patch anchor was not found; pinned upstream changed`)
  }
  return source.replace(before, after)
}

let utils = fs.readFileSync(utilsPath, 'utf8')
const helperIndex = utils.indexOf(helperMarker)
if (helperIndex >= 0) {
  const commentIndex = utils.lastIndexOf('\n// Project patch:', helperIndex)
  if (commentIndex < 0) {
    throw new Error('OpenCLI Gemini helper marker has no project patch boundary')
  }
  utils = utils.slice(0, commentIndex)
}
utils = `${utils.trimEnd()}${helperSource}\n`
fs.writeFileSync(utilsPath, utils)

let ask = fs.readFileSync(askPath, 'utf8')
ask = replaceOnce(
  ask,
  '  GEMINI_DOMAIN,\n  ensureGeminiPage,',
  '  GEMINI_DOMAIN,\n  attachGeminiFile,\n  ensureGeminiPage,',
  'Gemini ask import',
)
ask = replaceOnce(
  ask,
  "        { name: 'thinking', required: false, help: 'Thinking level: standard or extended (omitted = leave unchanged)', default: null },\n",
  "        { name: 'thinking', required: false, help: 'Thinking level: standard or extended (omitted = leave unchanged)', default: null },\n        { name: 'file', required: false, help: 'Attach one local image before sending the prompt' },\n",
  'Gemini ask file option',
)
ask = replaceOnce(
  ask,
  '        const before = await readGeminiSnapshot(page);\n        await sendGeminiMessage(page, prompt);',
  '        if (kwargs.file) await attachGeminiFile(page, kwargs.file);\n        const before = await readGeminiSnapshot(page);\n        await sendGeminiMessage(page, prompt);',
  'Gemini ask attachment call',
)
fs.writeFileSync(askPath, ask)

const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'))
const askEntry = manifest.find(
  (entry) => entry?.site === 'gemini' && entry?.name === 'ask',
)
if (!askEntry || !Array.isArray(askEntry.args)) {
  throw new Error('OpenCLI Gemini ask manifest entry was not found')
}
if (!askEntry.args.some((argument) => argument?.name === 'file')) {
  askEntry.args.push({
    name: 'file',
    type: 'str',
    required: false,
    help: 'Attach one local image before sending the prompt',
  })
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`)
}

console.log('Applied project Gemini image-upload patch to OpenCLI 1.8.6')
