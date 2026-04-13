import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'fs/promises';
import path from 'path';
import os from 'os';
import { scanWorkflows } from '../../src/server/scanner.js';

describe('scanWorkflows', () => {
  let tmpDir: string;

  beforeEach(async () => {
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'fdsx-scanner-test-'));
  });

  afterEach(async () => {
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  async function writeYaml(filePath: string, content: string): Promise<void> {
    const dir = path.dirname(filePath);
    if (dir !== tmpDir) {
      await fs.mkdir(dir, { recursive: true });
    }
    await fs.writeFile(filePath, content, 'utf-8');
  }

  it('discovers valid workflow YAML files in a directory', async () => {
    await writeYaml(
      path.join(tmpDir, 'workflow.yaml'),
      `name: Test Workflow
start_at: first
states:
  first:
    type: task
    provider: system
    command: echo hello
    result_path: $.result
`,
    );

    const results = await scanWorkflows(tmpDir);

    expect(results).toHaveLength(1);
    expect(results[0].name).toBe('Test Workflow');
    expect(results[0].relativePath).toBe('workflow.yaml');
  });

  it('skips non-YAML files', async () => {
    await writeYaml(path.join(tmpDir, 'readme.txt'), 'not a yaml');
    await writeYaml(
      path.join(tmpDir, 'workflow.yaml'),
      `name: Test
start_at: first
states:
  first:
    type: task
    provider: system
    command: echo
`,
    );

    const results = await scanWorkflows(tmpDir);

    expect(results).toHaveLength(1);
    expect(results[0].name).toBe('Test');
  });

  it('skips YAML files that are not fdsx workflows', async () => {
    await writeYaml(
      path.join(tmpDir, 'docker-compose.yaml'),
      `version: '3'
services:
  web:
    image: nginx
`,
    );
    await writeYaml(
      path.join(tmpDir, 'workflow.yaml'),
      `name: Real Workflow
start_at: first
states:
  first:
    type: task
    provider: system
    command: echo
`,
    );

    const results = await scanWorkflows(tmpDir);

    expect(results).toHaveLength(1);
    expect(results[0].name).toBe('Real Workflow');
  });

  it('discovers workflows in nested subdirectories', async () => {
    await writeYaml(
      path.join(tmpDir, 'subdir', 'nested.yaml'),
      `name: Nested Workflow
start_at: first
states:
  first:
    type: task
    provider: system
    command: echo
`,
    );

    const results = await scanWorkflows(tmpDir);

    expect(results).toHaveLength(1);
    expect(results[0].name).toBe('Nested Workflow');
    expect(results[0].relativePath).toBe('subdir/nested.yaml');
  });

  it('returns empty array for empty directory', async () => {
    const results = await scanWorkflows(tmpDir);
    expect(results).toHaveLength(0);
  });

  it('derives name from YAML name field', async () => {
    await writeYaml(
      path.join(tmpDir, 'workflow.yaml'),
      `name: My Workflow Name
start_at: first
states:
  first:
    type: task
    provider: system
    command: echo
`,
    );

    const results = await scanWorkflows(tmpDir);

    expect(results[0].name).toBe('My Workflow Name');
  });

  it('falls back to filename when name field is missing', async () => {
    await writeYaml(
      path.join(tmpDir, 'my_workflow.yaml'),
      `start_at: first
states:
  first:
    type: task
    provider: system
    command: echo
`,
    );

    const results = await scanWorkflows(tmpDir);

    expect(results[0].name).toBe('my_workflow');
  });

  it('silently skips malformed YAML files', async () => {
    await writeYaml(path.join(tmpDir, 'bad.yaml'), 'invalid: yaml: content: [');
    await writeYaml(
      path.join(tmpDir, 'good.yaml'),
      `name: Good
start_at: first
states:
  first:
    type: task
    provider: system
    command: echo
`,
    );

    const results = await scanWorkflows(tmpDir);

    expect(results).toHaveLength(1);
    expect(results[0].name).toBe('Good');
  });

  it('sorts results alphabetically by name', async () => {
    await writeYaml(
      path.join(tmpDir, 'zebra.yaml'),
      `name: Zebra Workflow
start_at: first
states:
  first:
    type: task
    provider: system
    command: echo
`,
    );
    await writeYaml(
      path.join(tmpDir, 'alpha.yaml'),
      `name: Alpha Workflow
start_at: first
states:
  first:
    type: task
    provider: system
    command: echo
`,
    );

    const results = await scanWorkflows(tmpDir);

    expect(results[0].name).toBe('Alpha Workflow');
    expect(results[1].name).toBe('Zebra Workflow');
  });

  it('handles .yml extension', async () => {
    await writeYaml(
      path.join(tmpDir, 'workflow.yml'),
      `name: Yml Workflow
start_at: first
states:
  first:
    type: task
    provider: system
    command: echo
`,
    );

    const results = await scanWorkflows(tmpDir);

    expect(results).toHaveLength(1);
    expect(results[0].name).toBe('Yml Workflow');
  });
});
