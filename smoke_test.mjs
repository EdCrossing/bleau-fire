/* Execute the built page's script against a minimal DOM stub.
 *
 * `node --check` only parses. It passed a build in which `ctxImgs` was referenced but never
 * defined, because that is a *runtime* ReferenceError — apply() threw on first call, the whole
 * script died, and the page shipped with no map and no legends. This runs the code.
 *
 *   node smoke_test.mjs data/web/index.html
 */
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const file = process.argv[2] || 'data/web/index.html';
const html = readFileSync(file, 'utf8');
const script = html.slice(html.lastIndexOf('<script>') + 8, html.lastIndexOf('</script>'));

// Element ids the script looks up. A missing one returns null and usually throws on use,
// which is exactly the class of bug this is here to catch.
const IDS = ['world', 'mapbox', 'cv', 'ctxcv', 'tip', 'coord', 'scale', 'legend', 'legwrap',
  'bases', 'overs', 'ptopts', 'ctxopts', 'opacity', 'time', 'framelab', 'prev', 'next',
  'skipempty', 'skipstat', 'zin', 'zout', 'zfire', 'zall', 'togglepanel', 'circuitbody'];

const listeners = [];
function mkEl(id = '') {
  const el = {
    id, style: {}, dataset: {}, classList: {
      add() {}, remove() {}, toggle() {}, contains: () => false,
    },
    children: [], value: '0', max: '0', checked: false, textContent: '', innerHTML: '',
    complete: true, naturalWidth: 100, width: 0, height: 0,
    clientWidth: 1200, clientHeight: 800,
    appendChild(c) { this.children.push(c); return c; },
    addEventListener(t, f) { listeners.push([this, t, f]); },
    removeEventListener() {},
    setPointerCapture() {}, releasePointerCapture() {},
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 1200, height: 800 }),
    querySelector: () => mkEl(),
    querySelectorAll: () => [],
    closest: () => null,
    matches: () => false,
    scrollIntoView() {},
    getAttribute(k) { return this[k] ?? null; },
    setAttribute(k, v) { this[k] = v; },
    getContext: () => ({
      setTransform() {}, clearRect() {}, beginPath() {}, arc() {}, fill() {}, stroke() {},
      moveTo() {}, lineTo() {}, fillText() {}, strokeText() {}, drawImage() {}, measureText: () => ({ width: 10 }),
    }),
  };
  return el;
}
const els = Object.fromEntries(IDS.map(i => [i, mkEl(i)]));

const document = {
  getElementById: id => els[id] ?? null,
  createElement: () => mkEl(),
  querySelectorAll: () => [],
  documentElement: mkEl(),
  addEventListener(t, f) { listeners.push([document, t, f]); },
};
class Image { constructor() { Object.assign(this, mkEl()); } set src(v) { this._src = v; } get src() { return this._src; } }
const sandbox = {
  document, Image, console,
  window: { devicePixelRatio: 1, addEventListener(t, f) { listeners.push([null, t, f]); } },
  getComputedStyle: () => ({ getPropertyValue: () => '#fff' }),
  ResizeObserver: class { observe() {} },
  setInterval: () => 0, clearInterval() {}, setTimeout: () => 0,
  Math, JSON, Object, Array, Number, String, Boolean, Set, Map, isNaN, parseInt, parseFloat,
};
sandbox.globalThis = sandbox;

let failed = false;
try {
  vm.createContext(sandbox);
  new vm.Script(script, { filename: 'page.js' }).runInContext(sandbox, { timeout: 30000 });
  console.log('  script executed with no runtime error');
} catch (e) {
  console.error('  RUNTIME ERROR:', e.message);
  console.error(String(e.stack).split('\n').slice(0, 4).join('\n'));
  failed = true;
}

// Exercise the interactive paths too — most of the code only runs on an event.
let fired = 0;
for (const [, type, fn] of listeners) {
  if (['pointermove', 'wheel', 'pointerdown', 'pointerup', 'pointerleave'].includes(type)) continue;
  try {
    fn({ target: mkEl(), clientX: 10, clientY: 10, key: 'ArrowRight', deltaY: 1,
         preventDefault() {}, pointerId: 1 });
    fired++;
  } catch (e) {
    console.error(`  RUNTIME ERROR in "${type}" handler:`, e.message);
    failed = true;
  }
}
console.log(`  exercised ${fired} event handlers`);
process.exit(failed ? 1 : 0);
