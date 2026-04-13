import fs from 'fs';

const filePath = 'dist/server/server/cli.js';
const content = fs.readFileSync(filePath, 'utf-8');
fs.writeFileSync(filePath, `#!/usr/bin/env node\n${content}`);
fs.chmodSync(filePath, 0o755);
