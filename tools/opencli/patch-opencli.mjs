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
const videoPath = path.join(geminiDir, 'video.js')
const twitterUtilsPath = path.join(
  runtimeDir,
  'node_modules',
  '@jackwener',
  'opencli',
  'clis',
  'twitter',
  'utils.js',
)
const twitterPostPath = path.join(
  runtimeDir,
  'node_modules',
  '@jackwener',
  'opencli',
  'clis',
  'twitter',
  'post.js',
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
  // A replacement string treats `$&`, `$'`, and similar sequences specially.
  // Adapter source contains React's `__reactProps$` key, so use a replacer
  // function to insert every patch byte literally.
  return source.replace(before, () => after)
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

fs.copyFileSync(
  path.join(runtimeDir, 'patches', 'gemini-video-command.js'),
  videoPath,
)

// Chrome's extension debugger can enable file-chooser interception but still
// receive no Page.fileChooserOpened event when X exposes a hidden file input.
// Upstream already has a bounded DataTransfer fallback for recoverable bridge
// failures; classify this chooser timeout narrowly so that fallback can run.
let twitterUtils = fs.readFileSync(twitterUtilsPath, 'utf8')
twitterUtils = replaceOnce(
  twitterUtils,
  'return /unknown action|not supported|not[-\\s]?allowed|notallowederror/i.test(msg);',
  'return /unknown action|not supported|not[-\\s]?allowed|notallowederror|filechooseropened\\s+not\\s+received/i.test(msg);',
  'Twitter file chooser fallback',
)
fs.writeFileSync(twitterUtilsPath, twitterUtils)

let twitterPost = fs.readFileSync(twitterPostPath, 'utf8')
twitterPost = replaceOnce(
  twitterPost,
  '        const input = document.querySelector(${JSON.stringify(FILE_INPUT_SELECTOR)});',
  `        const inputs = Array.from(document.querySelectorAll(\${JSON.stringify(FILE_INPUT_SELECTOR)}));
        const input = inputs.find((candidate) => {
            const dialog = candidate.closest('[role="dialog"]');
            return dialog && (dialog.offsetParent !== null || dialog.getClientRects().length > 0);
        }) || inputs[0];`,
  'Twitter active composer input',
)
twitterPost = replaceOnce(
  twitterPost,
  `        input.dispatchEvent(new Event('change', { bubbles: true }));
        input.dispatchEvent(new Event('input', { bubbles: true }));
        return { ok: true };`,
  `        // X exposes duplicate hidden inputs and React does not observe an
        // untrusted DOM change event here. Invoke the active input's current
        // React handler when available; retain native events for non-React UI.
        const reactPropsKey = Object.keys(input).find((key) => key.startsWith('__reactProps$'));
        const reactOnChange = reactPropsKey && input[reactPropsKey]?.onChange;
        if (typeof reactOnChange === 'function') {
            const nativeEvent = new Event('change', { bubbles: true });
            reactOnChange({
                bubbles: true,
                currentTarget: input,
                defaultPrevented: false,
                isDefaultPrevented: () => false,
                isPropagationStopped: () => false,
                nativeEvent,
                persist() {},
                preventDefault() { nativeEvent.preventDefault(); },
                stopPropagation() { nativeEvent.stopPropagation(); },
                target: input,
                type: 'change',
            });
        } else {
            input.dispatchEvent(new Event('change', { bubbles: true }));
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }
        return { ok: true };`,
  'Twitter React file input notification',
)
fs.writeFileSync(twitterPostPath, twitterPost)

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
}

const videoEntry = {
  site: 'gemini',
  name: 'video',
  description: 'Create a Gemini Web video from ordered first/last frames and save it locally',
  access: 'write',
  domain: 'gemini.google.com',
  strategy: 'cookie',
  browser: true,
  args: [
    { name: 'prompt', type: 'str', required: false, positional: true, help: 'Create Video animation prompt' },
    { name: 'first', type: 'str', required: false, help: 'Local empty first-frame image' },
    { name: 'last', type: 'str', required: false, help: 'Local completed last-frame image' },
    { name: 'resume', type: 'str', required: false, help: 'Existing Gemini conversation URL to finish or download' },
    { name: 'aspect', type: 'str', default: '16:9', required: false, help: 'Final aspect ratio', choices: ['16:9', '9:16'] },
    { name: 'output', type: 'str', required: true, help: 'Local MP4 output path' },
    { name: 'timeout', type: 'int', default: 1800, required: false, help: 'Total generation and download timeout in seconds' },
  ],
  columns: ['status', 'file', 'aspect', 'link'],
  defaultFormat: 'plain',
  type: 'js',
  modulePath: 'gemini/video.js',
  sourceFile: 'gemini/video.js',
  navigateBefore: false,
  siteSession: 'persistent',
}
const videoIndex = manifest.findIndex(
  (entry) => entry?.site === 'gemini' && entry?.name === 'video',
)
if (videoIndex >= 0) manifest[videoIndex] = videoEntry
else manifest.push(videoEntry)
fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`)

console.log('Applied project Gemini media and Twitter React upload-fallback patches to OpenCLI 1.8.6')
