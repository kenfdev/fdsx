import fs from 'fs/promises';
import path from 'path';
import yaml from 'js-yaml';
import type { WorkflowFile } from '../shared/types.js';

function isFdsxWorkflow(obj: unknown): obj is Record<string, unknown> {
  if (typeof obj !== 'object' || obj === null) return false;
  const record = obj as Record<string, unknown>;
  return (
    typeof record['states'] === 'object' &&
    record['states'] !== null &&
    typeof record['start_at'] === 'string'
  );
}

function deriveWorkflowName(
  yamlName: unknown,
  filePath: string,
): string {
  if (typeof yamlName === 'string' && yamlName.length > 0) {
    return yamlName;
  }
  const base = path.basename(filePath);
  return base.replace(/\.(yaml|yml)$/, '');
}

export async function scanWorkflows(dirPath: string): Promise<WorkflowFile[]> {
  const results: WorkflowFile[] = [];

  const entries = await fs.readdir(dirPath, { recursive: true, withFileTypes: true });

  for (const entry of entries) {
    if (!entry.isFile()) continue;

    const fullPath = path.join(entry.parentPath || dirPath, entry.name);
    const ext = path.extname(fullPath).toLowerCase();
    if (ext !== '.yaml' && ext !== '.yml') continue;

    try {
      const content = await fs.readFile(fullPath, 'utf-8');
      const parsed = yaml.load(content);

      if (!isFdsxWorkflow(parsed)) continue;

      const name = deriveWorkflowName(parsed['name'], fullPath);
      const relativePath = path.relative(dirPath, fullPath).replace(/\\/g, '/');

      results.push({
        name,
        filePath: fullPath,
        relativePath,
      });
    } catch {
      continue;
    }
  }

  return results.sort((a, b) => a.name.localeCompare(b.name));
}
