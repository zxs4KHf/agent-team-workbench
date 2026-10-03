// Parse as an actual ES module without evaluating DOM code or sending requests.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..', 'web', 'v2', 'js');
let failures = 0;
for (const name of fs.readdirSync(root).filter(name => name.endsWith('.js'))) {
  try {
    new vm.SourceTextModule(fs.readFileSync(path.join(root, name), 'utf8'), { identifier: name });
    console.log('PARSE OK', name);
  } catch (error) {
    failures++;
    console.error('PARSE FAILED', name, error.stack);
  }
}
process.exitCode = failures ? 1 : 0;
