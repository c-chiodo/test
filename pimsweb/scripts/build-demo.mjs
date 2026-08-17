/* Build the sandbox as one self-contained HTML file.
 *
 * Vite emits index.html plus hashed JS/CSS; an Artifact has to be a single
 * document with no external requests, so this inlines them and drops the
 * result in dist-demo/. Run through `npm run build:demo`. */

import { readFileSync, writeFileSync, mkdirSync, readdirSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const dist = join(root, 'dist-demo')
const assets = join(dist, 'assets')

const files = readdirSync(assets)
const js = files.filter((name) => name.endsWith('.js'))
const css = files.filter((name) => name.endsWith('.css'))
if (js.length !== 1) throw new Error(`expected one JS chunk, found ${js.length}`)

const script = readFileSync(join(assets, js[0]), 'utf8')
const styles = css.map((name) => readFileSync(join(assets, name), 'utf8')).join('\n')

/* Emitted as a document fragment — title, styles, root, script — with no
 * <html>/<head>/<body> wrapper. Browsers render it directly when opened as a
 * file, and the Artifact host supplies its own skeleton around it. */
const html = `<title>PIMS Sandbox</title>
<style>${styles}</style>
<div id="root"></div>
<script type="module">
${script}
</script>
`

mkdirSync(dist, { recursive: true })
const out = join(dist, 'pims-sandbox.html')
writeFileSync(out, html, 'utf8')
console.log(`${out}  ${(Buffer.byteLength(html) / 1_048_576).toFixed(2)} MB`)
