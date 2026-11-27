/* (C) Copyright 2026, by Ross Richardson
 * Compile the matching chart readers and copy approved calculator bytes into a candidate.
 * @author ross richardson
 */
const fs = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');
const approved = require('./historical/approved.json');

function checked(bytes, expected, label) {
  if (createHash('sha256').update(bytes).digest('hex') !== expected)
    throw Error('Archived Visualiser file differs: ' + label);
  return bytes;
}

function buildReaders(args, staging, transform, currentFiles) {
  const imports = [], definitions = [], editions = [], currentRevisions = [];
  const legacy = Boolean(args['historical-source']);
  if (args['historical-build'] && !legacy) throw Error('Supply the matching historical reader sources');
  for (const edition of approved) {
    if (!args['reader-sources'] && !(legacy && edition.revision === 'f6384fa6ea856802dadd7a115aca02c719142bdc')) continue;
    if (Object.keys(currentFiles).every(name => currentFiles[name] === edition.files[name]) &&
        edition.node === process.version) {
      currentRevisions.push(edition.revision);
      continue;
    }
    const short = edition.revision.slice(0, 8);
    const archive = args['historical-build'] || path.join(__dirname, 'historical', short);
    const manifest = JSON.parse(checked(fs.readFileSync(path.join(archive, 'build.json')),
      edition.manifest_sha256, short));
    if (manifest.revision !== edition.revision || manifest.toolchain.node !== edition.node ||
        !Object.keys(currentFiles).every(name => manifest.files[name] === edition.files[name]))
      throw Error('Inconsistent approved Visualiser edition ' + short);
    const folder = path.join(staging, edition.revision);
    fs.mkdirSync(folder);
    for (const [name, digest] of Object.entries(manifest.source_hashes)) {
      if (!name.endsWith('.js')) continue;
      const sourceRoot = legacy ? path.join(args['historical-source'], 'src') :
        path.join(args['reader-sources'], edition.revision);
      const source = checked(fs.readFileSync(path.join(sourceRoot, name)), digest, name).toString();
      fs.writeFileSync(path.join(folder, name), transform(source, name));
    }
    const prefix = legacy ? 'historical-' : 'historical-' + edition.revision + '-';
    const files = ['runner.cjs', 'calculation.cjs', 'dependencies.LICENSE.txt','calculation.cjs.LICENSE.txt'];
    if (manifest.files['visualiser.LICENSE.txt']) files.push('visualiser.LICENSE.txt');
    for (const name of files) {
      const bytes = fs.readFileSync(path.join(archive, name));
      fs.writeFileSync(path.join(args.output, prefix + name), checked(bytes, manifest.files[name], name));
    }
    const {revision, node} = edition;
    editions.push({revision, node, files: edition.files});
    const identifier = 'Reader' + editions.length;
    imports.push('import ' + identifier + ' from "./' + revision + '/App";');
    definitions.push('{revision:' + JSON.stringify(revision) + ', files:' +
      JSON.stringify(edition.files) + ', App:' + identifier + '}');
  }
  fs.writeFileSync(path.join(staging, 'historical-reader.js'), imports.join('\n') +
    '\nexport const editions=[' + definitions.join(',') + '];\nexport const currentFiles=' +
    JSON.stringify(currentFiles) + ';\nexport const currentRevisions=' + JSON.stringify(currentRevisions) + ';\n');
  if (args['reader-sources']) return {historical_editions: editions};
  return editions.length ? {historical: editions[0]} : {};
}
module.exports = {buildReaders};
