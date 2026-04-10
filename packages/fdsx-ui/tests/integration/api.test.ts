import { describe, it, expect } from 'vitest';
import request from 'supertest';
import http from 'http';
import path from 'path';
import { createApp } from '../../src/server/server.js';

const fixturesDir = path.join(__dirname, '../fixtures');

describe('API', () => {
  const app = createApp(fixturesDir);

  it('GET /api/workflows returns list of discovered fixtures', async () => {
    const response = await request(app).get('/api/workflows');

    expect(response.status).toBe(200);
    expect(Array.isArray(response.body)).toBe(true);
    const names = response.body.map((w: { name: string }) => w.name);
    expect(names).toContain('Linear Workflow');
    expect(names).toContain('Choice Workflow');
    expect(names).toContain('Parallel Workflow');
    expect(names).toContain('Pass Wait Map Workflow');
  });

  it('GET /api/workflows/linear.yaml returns nodes and edges', async () => {
    const response = await request(app).get('/api/workflows/linear.yaml');

    expect(response.status).toBe(200);
    expect(response.body.workflow).toBeDefined();
    expect(response.body.workflow.name).toBe('Linear Workflow');
    expect(Array.isArray(response.body.nodes)).toBe(true);
    expect(Array.isArray(response.body.edges)).toBe(true);
    expect(response.body.nodes.length).toBeGreaterThan(0);
    expect(response.body.edges.length).toBeGreaterThan(0);
  });

  it('GET /api/workflows/nonexistent.yaml returns 404', async () => {
    const response = await request(app).get('/api/workflows/nonexistent.yaml');

    expect(response.status).toBe(404);
    expect(response.body.error).toBe('Workflow not found');
  });

  it('GET /api/workflows/linear.yaml/reload returns fresh data', async () => {
    const response = await request(app).get('/api/workflows/linear.yaml/reload');

    expect(response.status).toBe(200);
    expect(response.body.workflow).toBeDefined();
    expect(Array.isArray(response.body.nodes)).toBe(true);
  });

  it('malformed YAML returns 422', async () => {
    const response = await request(app).get('/api/workflows/not-a-workflow.yaml');

    expect(response.status).toBe(422);
    expect(response.body.error).toBeDefined();
  });

  it('GET /api/workflows/choice.yaml returns choice edges with labels', async () => {
    const response = await request(app).get('/api/workflows/choice.yaml');

    expect(response.status).toBe(200);
    const choiceEdges = response.body.edges.filter(
      (e: { edgeType?: string }) => e.edgeType === 'choice',
    );
    expect(choiceEdges.length).toBeGreaterThan(0);
  });

  it('GET /api/workflows/parallel.yaml returns parallel edge type', async () => {
    const response = await request(app).get('/api/workflows/parallel.yaml');

    expect(response.status).toBe(200);
    const parallelEdges = response.body.edges.filter(
      (e: { edgeType?: string }) => e.edgeType === 'parallel',
    );
    expect(parallelEdges.length).toBe(1);
  });

  it('rejects path traversal with .. segments', async () => {
    const server = app.listen(0);
    const addr = server.address() as { port: number };
    try {
      const res = await new Promise<{ statusCode: number; body: string }>((resolve, reject) => {
        const req = http.request(
          {
            hostname: '127.0.0.1',
            port: addr.port,
            path: '/api/workflows/../../etc/passwd',
            method: 'GET',
          },
          (res) => {
            let body = '';
            res.on('data', (chunk: Buffer) => { body += chunk.toString(); });
            res.on('end', () => resolve({ statusCode: res.statusCode!, body }));
          },
        );
        req.on('error', reject);
        req.end();
      });

      expect(res.statusCode).toBe(404);
      expect(JSON.parse(res.body)).toEqual({ error: 'Workflow not found' });
    } finally {
      server.close();
    }
  });
});
