import { fileURLToPath } from 'url';
import express, { type Express, type Request, type Response } from 'express';
import fs from 'fs/promises';
import fsSync from 'fs';
import path from 'path';
import { scanWorkflows } from './scanner.js';
import { parseWorkflow, WorkflowParseError } from './parser.js';
import { transformWorkflow } from './graph.js';
import type { WorkflowFile, Workflow } from '../shared/types.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function resolveClientDist(): string {
  let dir = __dirname;
  while (dir !== path.dirname(dir)) {
    const candidate = path.join(dir, 'dist', 'client');
    if (fsSync.existsSync(candidate)) return candidate;
    if (fsSync.existsSync(path.join(dir, 'package.json'))) break;
    dir = path.dirname(dir);
  }
  return path.join(__dirname, '..', '..', '..', 'dist', 'client');
}

const CLIENT_DIST = resolveClientDist();

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

type PromptFileResult =
  | { kind: 'ok'; absolutePath: string }
  | { kind: 'workflow-not-found' }
  | { kind: 'outside-workspace' }
  | { kind: 'not-found' }
  | { kind: 'read-error'; message: string };

async function resolvePromptFileWithinWorkspace(
  workflowDir: string,
  workflowRelativePath: string,
  promptRelativePath: string,
): Promise<PromptFileResult> {
  if (path.isAbsolute(promptRelativePath)) {
    return { kind: 'outside-workspace' };
  }

  let realBase: string;
  try {
    realBase = await fs.realpath(workflowDir);
  } catch {
    return { kind: 'workflow-not-found' };
  }

  const fullYamlPath = path.resolve(workflowDir, workflowRelativePath);
  const normalizedBase = path.resolve(workflowDir);
  if (!fullYamlPath.startsWith(normalizedBase + path.sep) && fullYamlPath !== normalizedBase) {
    return { kind: 'workflow-not-found' };
  }

  let yamlRealPath: string;
  try {
    yamlRealPath = await fs.realpath(fullYamlPath);
  } catch {
    return { kind: 'workflow-not-found' };
  }

  if (!yamlRealPath.startsWith(realBase + path.sep) && yamlRealPath !== realBase) {
    return { kind: 'workflow-not-found' };
  }

  const yamlDir = path.dirname(yamlRealPath);
  const candidatePath = path.resolve(yamlDir, promptRelativePath);

  // Pre-realpath containment check: catches .. traversal even for non-existent files.
  if (!candidatePath.startsWith(realBase + path.sep)) {
    return { kind: 'outside-workspace' };
  }

  let promptRealPath: string;
  try {
    promptRealPath = await fs.realpath(candidatePath);
  } catch (err) {
    const nodeErr = err as NodeJS.ErrnoException;
    if (nodeErr.code === 'ENOENT') {
      return { kind: 'not-found' };
    }
    return { kind: 'read-error', message: nodeErr.message ?? String(err) };
  }

  if (!promptRealPath.startsWith(realBase + path.sep)) {
    return { kind: 'outside-workspace' };
  }

  return { kind: 'ok', absolutePath: promptRealPath };
}

function collectPromptFiles(workflow: Workflow): string[] {
  const files: string[] = [];
  for (const state of Object.values(workflow.states)) {
    if (state.type === 'task' && state.promptFile) {
      files.push(state.promptFile);
    } else if (state.type === 'parallel') {
      for (const branch of state.branches) {
        if (branch.promptFile) files.push(branch.promptFile);
      }
    } else if (state.type === 'map') {
      for (const iterState of state.iterator.states) {
        if (iterState.promptFile) files.push(iterState.promptFile);
      }
    }
  }
  return files;
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

  app.get(/^\/api\/workflows\/(.+)\/prompt$/, async (req: Request, res: Response) => {
    const match = req.path.match(/^\/api\/workflows\/(.+)\/prompt$/);
    if (!match) {
      res.status(404).json({ error: 'not-found' });
      return;
    }
    const workflowPath = match[1];
    const file = typeof req.query.file === 'string' ? req.query.file : undefined;
    if (!file) {
      res.status(400).json({ error: 'missing-file' });
      return;
    }

    const result = await resolvePromptFileWithinWorkspace(workflowDir, workflowPath, file);
    if (result.kind === 'ok') {
      // Validate the requested file is actually declared as prompt_file in the workflow.
      // This prevents the endpoint from becoming an arbitrary workspace file-read API.
      try {
        const yamlFullPath = path.resolve(workflowDir, workflowPath);
        const yamlContent = await fs.readFile(yamlFullPath, 'utf-8');
        const workflow = parseWorkflow(yamlContent, yamlFullPath);
        const validFiles = collectPromptFiles(workflow);
        if (!validFiles.includes(file)) {
          res.status(404).json({ error: 'not-found', file });
          return;
        }
      } catch {
        // Workflow parse failure → deny; path was already validated above
        res.status(404).json({ error: 'not-found', file });
        return;
      }

      try {
        const contents = await fs.readFile(result.absolutePath, 'utf-8');
        res.json({ contents, file });
      } catch (err) {
        const nodeErr = err as NodeJS.ErrnoException;
        res.status(404).json({ error: 'read-error', file, message: nodeErr.message ?? String(err) });
      }
    } else if (result.kind === 'outside-workspace') {
      res.status(404).json({ error: 'outside-workspace', file });
    } else if (result.kind === 'not-found') {
      res.status(404).json({ error: 'not-found', file });
    } else if (result.kind === 'workflow-not-found') {
      res.status(404).json({ error: 'not-found', file });
    } else {
      res.status(404).json({ error: 'read-error', file });
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
