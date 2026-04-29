import { describe, it, expect } from 'vitest';
import request from 'supertest';
import http from 'http';
import path from 'path';
import { writeFile, chmod, unlink } from 'fs/promises';
import { createApp } from '../../src/server/server.js';

const fixturesDir = path.join(__dirname, '../fixtures');

describe('Prompt file endpoint', () => {
  const app = createApp(fixturesDir);

  // ── 200 happy path ──────────────────────────────────────────────────────────

  it('returns 200 with { contents, file } for a valid prompt file', async () => {
    const response = await request(app)
      .get('/api/workflows/prompt-file.yaml/prompt')
      .query({ file: 'prompts/my-prompt.txt' });

    expect(response.status).toBe(200);
    expect(response.headers['content-type']).toMatch(/application\/json/);
    expect(typeof response.body.contents).toBe('string');
    expect(response.body.contents.trim()).toBe('You are a helpful assistant.');
    expect(response.body.file).toBe('prompts/my-prompt.txt');
  });

  // ── 400 missing / empty file param ─────────────────────────────────────────

  it('returns 400 missing-file when ?file= query param is absent', async () => {
    const response = await request(app)
      .get('/api/workflows/prompt-file.yaml/prompt');

    expect(response.status).toBe(400);
    expect(response.body.error).toBe('missing-file');
  });

  it('returns 400 missing-file when ?file= is an empty string', async () => {
    const response = await request(app)
      .get('/api/workflows/prompt-file.yaml/prompt')
      .query({ file: '' });

    expect(response.status).toBe(400);
    expect(response.body.error).toBe('missing-file');
  });

  // ── 404 not-found ───────────────────────────────────────────────────────────

  it('returns 404 not-found when the prompt file does not exist', async () => {
    const response = await request(app)
      .get('/api/workflows/prompt-file.yaml/prompt')
      .query({ file: 'prompts/nonexistent.txt' });

    expect(response.status).toBe(404);
    expect(response.body.error).toBe('not-found');
    expect(response.body.file).toBe('prompts/nonexistent.txt');
  });

  it('returns 404 when the workflow YAML does not exist', async () => {
    const response = await request(app)
      .get('/api/workflows/nonexistent.yaml/prompt')
      .query({ file: 'prompts/my-prompt.txt' });

    expect(response.status).toBe(404);
  });

  // ── 404 outside-workspace (path traversal) ──────────────────────────────────

  it('returns 404 outside-workspace when ?file= contains .. segments (via raw HTTP)', async () => {
    const server = app.listen(0);
    const addr = server.address() as { port: number };
    try {
      const res = await new Promise<{ statusCode: number; body: string }>((resolve, reject) => {
        const req = http.request(
          {
            hostname: '127.0.0.1',
            port: addr.port,
            path: '/api/workflows/prompt-file.yaml/prompt?file=../../etc/passwd',
            method: 'GET',
          },
          (res) => {
            let body = '';
            res.on('data', (chunk: Buffer) => {
              body += chunk.toString();
            });
            res.on('end', () => resolve({ statusCode: res.statusCode!, body }));
          },
        );
        req.on('error', reject);
        req.end();
      });

      expect(res.statusCode).toBe(404);
      const parsed = JSON.parse(res.body);
      expect(parsed.error).toBe('outside-workspace');
    } finally {
      server.close();
    }
  });

  it('returns 404 outside-workspace when ?file= is an absolute path', async () => {
    const response = await request(app)
      .get('/api/workflows/prompt-file.yaml/prompt')
      .query({ file: '/etc/passwd' });

    expect(response.status).toBe(404);
    expect(response.body.error).toBe('outside-workspace');
  });

  // ── 404 missing workflowDir (Finding 1) ────────────────────────────────────

  it('returns 404 with JSON body when workflowDir does not exist (not a 500)', async () => {
    const missingApp = createApp('/definitely/missing-workspace-dir');
    const response = await request(missingApp)
      .get('/api/workflows/any.yaml/prompt')
      .query({ file: 'prompts/my-prompt.txt' });

    expect(response.status).toBe(404);
    expect(response.headers['content-type']).toMatch(/application\/json/);
    // Must NOT be 500 or an empty body
    expect(response.body).toBeTruthy();
  });

  // ── 404 allowlist (Finding 2) ───────────────────────────────────────────────

  it('returns 404 not-found when file is inside workspace but not declared in workflow', async () => {
    // prompt-file.yaml declares only 'prompts/my-prompt.txt' and 'prompts/unreadable-prompt.txt'.
    // Requesting another fixture file that physically exists but is not declared must be rejected.
    const response = await request(app)
      .get('/api/workflows/prompt-file.yaml/prompt')
      .query({ file: 'prompt-file.yaml' }); // exists in fixtures but not a prompt_file

    expect(response.status).toBe(404);
    expect(response.body.error).toBe('not-found');
  });

  it('rejects a workspace file that exists but is not declared as prompt_file', async () => {
    const extra = path.join(fixturesDir, 'prompts', 'extra.txt');
    await writeFile(extra, 'extra content');
    try {
      // This workflow only declares 'prompts/my-prompt.txt' and 'prompts/unreadable-prompt.txt',
      // not 'prompts/extra.txt'.
      const response = await request(app)
        .get('/api/workflows/prompt-file.yaml/prompt')
        .query({ file: 'prompts/extra.txt' });
      expect(response.status).toBe(404);
      expect(response.body.error).toBe('not-found');
    } finally {
      await unlink(extra).catch(() => {});
    }
  });

  // ── 404 read-error ──────────────────────────────────────────────────────────

  it('returns 404 read-error when the prompt file exists but is unreadable', async () => {
    // Create a temporary file inside the fixtures/prompts directory, remove
    // read permissions (chmod 000), then confirm the endpoint returns read-error.
    // This works because the test process runs as a non-root user.
    const unreadablePath = path.join(fixturesDir, 'prompts', 'unreadable-prompt.txt');
    await writeFile(unreadablePath, 'secret content', 'utf-8');
    await chmod(unreadablePath, 0o000);

    try {
      const response = await request(app)
        .get('/api/workflows/prompt-file.yaml/prompt')
        .query({ file: 'prompts/unreadable-prompt.txt' });

      expect(response.status).toBe(404);
      expect(response.body.error).toBe('read-error');
    } finally {
      // Restore permissions before deleting so cleanup always succeeds.
      await chmod(unreadablePath, 0o644).catch(() => {});
      await unlink(unreadablePath).catch(() => {});
    }
  });
});
