/* (C) Copyright 2026, by Ross Richardson
 * Compare a fictional MultiRun experiment with the upstream folder reader and released calculation.
 * @author ross richardson
 */
const fs = require('node:fs');
const path = require('node:path');
const {isDeepStrictEqual} = require('node:util');
const [checkout, dependencies, candidate, released, work, reportFile] = process.argv.slice(2);
const webpack = require(path.join(dependencies, 'webpack'));

function folder(name, entries) {
  return {kind: 'directory', name, async *values() { yield* entries; }};
}

function fixture() {
  const cases = [];
  for (const role of ['baseline', 'scenario_1']) {
    for (const seed of ['606', '607']) {
      const people = ['time,idBu,wgt,demAge,demMaleFlag,healthMentalMcs,careReceivedFlag'];
      const benefits = ['time,id_BenefitUnit,region,careProvidedFlag'];
      for (const year of [2019, 2020, 2021, 2022]) {
        const small = (year === 2019 && role === 'scenario_1') || (year === 2020 && role === 'baseline');
        const count = year === 2021 ? 12 : small && seed === '606' ? 19 : 20;
        benefits.push([year, 1, 'UKI', 1].join(','));
        for (let i = 0; i < count; i++) {
          const value = (role === 'baseline' ? 40 : 45) + (seed === '607' ? 2 : 0);
          people.push([year, 1, 1, year === 2020 ? 103 : 30, 'Female', value, 2].join(','));
        }
      }
      cases.push({role, seed, person: people.join('\n') + '\n', benefit: benefits.join('\n') + '\n'});
    }
  }
  return cases;
}

async function folderResults(cases) {
  const entry = path.join(checkout, 'src', 'localFolderParser.js');
  await new Promise((resolve, reject) => {
    webpack({mode: 'production', target: 'node', entry, devtool: false,
      output: {path: work, filename: 'folder.cjs', library: {type: 'commonjs2'}},
      resolve: {modules: [dependencies]}, optimization: {minimize: false}},
    (err, stats) => err || stats.hasErrors() ? reject(err || Error(stats.toString({errors:true}))) : resolve());
  });
  const asFile = (name, text) => ({kind:'file', name, getFile:async () => new Blob([text])});
  const branches = ['baseline', 'scenario_1'].map(role => folder(role, cases.filter(c => c.role === role)
    .map(c => folder('run_' + c.seed + '_fixture', [folder('csv',
      [asFile('Person.csv', c.person), asFile('BenefitUnit.csv', c.benefit)])]))));
  return require(path.join(work, 'folder.cjs')).parseLocalFolder(folder('fixture', branches), message => {
    if (/^warning:/i.test(message)) throw Error(message);
  });
}

async function hostedResults(build, cases, label) {
  const {main} = require(path.join(build, 'runner.cjs'));
  const metrics = [];
  for (const sample of cases) {
    const fds = [];
    try {
      for (const kind of ['person', 'benefit']) {
        const file = path.join(work, kind + '.csv');
        fs.writeFileSync(file, sample[kind]);
        fds.push(fs.openSync(file, 'r'));
      }
      const output = path.join(work, label + '-' + sample.role + '-' + sample.seed + '.json');
      await main({operation:'run', role:sample.role, run:sample.seed, person_fd:fds[0], benefit_fd:fds[1], output});
      metrics.push(output);
    } finally {
      for (const fd of fds) fs.closeSync(fd);
    }
  }
  const output = path.join(work, label + '.json');
  await main({operation:'aggregate', metrics, output});
  return JSON.parse(fs.readFileSync(output));
}

const dimensions = ['scenario','year','module','variable','variable_value','stratifier','stratifier_value','metric_type'];
function index(rows) {
  const map = new Map();
  if (!Array.isArray(rows) || !rows.length) throw Error('The fixture returned no chart rows');
  for (const row of rows) {
    const key = JSON.stringify(dimensions.map(name => row[name]));
    if (map.has(key)) throw Error('Duplicate chart row ' + key);
    map.set(key, row);
  }
  return map;
}

function differences(before, after) {
  const changes = [];
  for (const key of [...new Set([...before.keys(), ...after.keys()])].sort()) {
    const old = before.get(key), next = after.get(key);
    if (!isDeepStrictEqual(old, next)) changes.push({before:old ?? null, after:next ?? null});
  }
  return changes;
}

async function compare() {
  const cases = fixture();
  const upstream = index(JSON.parse(JSON.stringify(await folderResults(cases))));
  const hosted = index(await hostedResults(candidate, cases, 'candidate'));
  const prior = index(await hostedResults(released, cases, 'released'));
  const parity = differences(upstream, hosted), changed = differences(prior, hosted);
  const observations = [...hosted.values()];
  const coverage = {paired:observations.filter(row => Number.isFinite(row.paired_mean_delta)).length,
    suppressed_baseline:observations.filter(row => row.scenario === 'baseline' && row.mean_value === null).length};
  fs.writeFileSync(reportFile, JSON.stringify({
    coverage, hosted_rows:hosted.size, upstream_folder_rows:upstream.size, released_rows:prior.size,
    upstream_differences:parity, release_differences:changed
  }) + '\n');
  if (!coverage.paired) throw Error('The candidate produced no paired observations for the fixture');
  if (parity.length) throw Error(parity.length + ' chart rows differ from upstream local-folder results');
}
compare().catch(error => {console.error(error); process.exitCode = 1;});
