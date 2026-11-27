/* (C) Copyright 2026, by Ross Richardson
 * Open a real fictional MultiRun export offline and check the calculator-specific reader and chart.
 * @author ross richardson
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const {test} = require('node:test');
const {JSDOM} = require(path.join(process.env.SIMPATHS_VISUALISER_DEPENDENCIES, 'jsdom'));
const [build, folder] = process.argv.slice(2);
const manifest = JSON.parse(fs.readFileSync(path.join(folder, 'manifest.json')));
async function wait(check) {
  const until = Date.now() + 5000;
  while (!check()) {
    if (Date.now() > until) throw Error('The offline reader did not render the requested chart');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}
for (const variant of ['original', 'without revision hint', 'conflicting checksum']) {
  test('open downloaded comparison ' + variant, async () => {
    const dom = new JSDOM(fs.readFileSync(path.join(build, 'index.html'), 'utf8'), {
      url:'https://localhost/visualiser/', runScripts:'outside-only', pretendToBeVisual:true
    });
    try {
      const w = dom.window, doc = w.document, reads = [], requests = [];
      Object.assign(w, {TextEncoder, TextDecoder, Blob, URLSearchParams, scrollTo() {}});
      Object.defineProperty(w, 'crypto', {value:webcrypto});
      w.ResizeObserver = class {observe() {} disconnect() {}};
      w.SVGElement.prototype.getBBox = () => ({width:40,height:12,x:0,y:0});
      w.SVGElement.prototype.getComputedTextLength = () => 40;
      w.fetch = async url => { requests.push(url); throw Error('Unexpected network request'); };
      function directory(root) {
        return {getDirectoryHandle:async name => directory(path.join(root,name)),
          getFileHandle:async name => ({getFile:async () => {
            reads.push(name);
            let bytes = fs.readFileSync(path.join(root, name));
            if (root === folder && name === 'manifest.json') {
              const value = structuredClone(manifest);
              if (variant === 'without revision hint') delete value.catalogue.calculator_revision;
              if (variant === 'conflicting checksum') value.provenance.calculation_files['runner.cjs'] = '1'.repeat(64);
              bytes = Buffer.from(JSON.stringify(value));
            }
            return {size:bytes.length, arrayBuffer:async () => Uint8Array.from(bytes).buffer};
          }})};
      }
      w.showDirectoryPicker = async () => directory(folder);
      new vm.Script(fs.readFileSync(path.join(build, 'visualiser.js'), 'utf8')).runInContext(dom.getInternalVMContext());
      const button = pattern => [...doc.querySelectorAll('button')].find(b => pattern.test(b.textContent.trim()));
      await wait(() => button(/^Open saved comparison$/) && doc.body.textContent.includes('Open a saved comparison, or select'));
      button(/^Open saved comparison$/).click();
      if (variant === 'conflicting checksum') {
        await wait(() => doc.body.textContent.includes('needs a reader that matches'));
        assert.deepEqual(reads, ['manifest.json']);
      } else {
        await wait(() => doc.querySelector('.vm-calculation-notice') && button(/^Health\s*▼$/));
        button(/^Health\s*▼$/).click();
        await wait(() => button(/^Mental Component Summary \(MCS\)$/));
        button(/^Mental Component Summary \(MCS\)$/).click();
        await wait(() => reads.some(name => /^[a-f0-9]{64}\.json$/.test(name)) &&
          button(/^↓ CSV$/) && !doc.body.textContent.includes('Loading chart'));
      }
      assert.deepEqual(requests, []);
    } catch (error) {
      throw Error(String(error.stack) + '\n' + (dom.window.document.querySelector('.vm-source-message')?.textContent ?? ''), {cause:error});
    } finally { dom.window.close(); }
  });
}
