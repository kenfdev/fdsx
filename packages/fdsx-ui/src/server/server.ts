import { fileURLToPath } from 'url';
import express, { type Express, type Request, type Response } from 'express';
import fs from 'fs/promises';
import path from 'path';
import { scanWorkflows } from './scanner.js';
import { parseWorkflow, WorkflowParseError } from './parser.js';
import { transformWorkflow } from './graph.js';
import type { WorkflowFile } from '../shared/types.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CLIENT_DIST = path.resolve(__dirname, '../../dist/client');

export interface WorkflowResponse {
  workflow: {
    name: string;
    description: string;
    startAt: string;
  };
  nodes: Array<{
    id: string;
    type: string;
    data: Record<string, unknown>;
    position: { x: number; y: number };
  }>;
  edges: Array<{
    id: string;
    source: string;
    target: string;
    label?: string;
    edgeType?: string;
    points?: Array<{ x: number; y: number }>;
  }>;
}

async function handleWorkflowRequest(
  workflowDir: string,
  relativePath: string,
  res: Response,
): Promise<void> {
  const fullPath = path.resolve(workflowDir, relativePath);
  const normalizedBase = path.resolve(workflowDir);
  if (!fullPath.startsWith(normalizedBase + path.sep) && fullPath !== normalizedBase) {
    res.status(404).json({ error: 'Workflow not found' });
    return;
  }

  let realPath: string;
  try {
    realPath = await fs.realpath(fullPath);
  } catch {
    res.status(404).json({ error: 'Workflow not found' });
    return;
  }
  const realBase = await fs.realpath(workflowDir);
  if (!realPath.startsWith(realBase + path.sep) && realPath !== realBase) {
    res.status(404).json({ error: 'Workflow not found' });
    return;
  }

  try {
    const content = await fs.readFile(realPath, 'utf-8');
    const workflow = parseWorkflow(content, fullPath);
    const { nodes, edges } = transformWorkflow(workflow);

    res.json({
      workflow: {
        name: workflow.name,
        description: workflow.description,
        startAt: workflow.startAt,
      },
      nodes,
      edges,
    });
  } catch (err) {
    if (err instanceof WorkflowParseError) {
      res.status(422).json({ error: err.message });
    } else {
      res.status(500).json({ error: 'Failed to parse workflow' });
    }
  }
}

export function createApp(workflowDir: string): Express {
  const app = express();

  app.get('/api/workflows', async (_req: Request, res: Response) => {
    try {
      const workflows: WorkflowFile[] = await scanWorkflows(workflowDir);
      res.json(workflows);
    } catch {
      res.status(500).json({ error: 'Failed to scan workflow directory' });
    }
  });

  app.get(/^\/api\/workflows\/([^/]+(?:\/[^/]*)*?)(?:\/reload)?$/, async (req: Request, res: Response) => {
    const match = req.path.match(/^\/api\/workflows\/([^/]+(?:\/[^/]*)*?)(?:\/reload)?$/);
    if (!match) {
      res.status(404).json({ error: 'Workflow not found' });
      return;
    }
    const workflowPath = match[1];
    await handleWorkflowRequest(workflowDir, workflowPath, res);
  });

  app.use(express.static(CLIENT_DIST));

  app.get(/.*/, (_req: Request, res: Response) => {
    res.sendFile(path.join(CLIENT_DIST, 'index.html'));
  });

  return app;
}
