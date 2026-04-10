import { Command } from 'commander';
import fs from 'fs';
import path from 'path';
import open from 'open';
import { createApp } from './server.js';

const DEFAULT_PORT = 3939;

async function main(): Promise<void> {
  const program = new Command();

  program
    .name('fdsx-ui')
    .description('Visualize fdsx workflow YAML files as interactive directed graphs')
    .argument('[path]', 'Path to workflow directory', process.cwd())
    .option('-p, --port <number>', 'Port to listen on', String(DEFAULT_PORT))
    .option('--no-open', 'Suppress browser auto-open')
    .action(async (dirPath: string, options: { port: string; open: boolean }) => {
      const resolvedPath = path.resolve(dirPath);

      try {
        const stat = fs.statSync(resolvedPath);
        if (!stat.isDirectory()) {
          console.error(`Error: Path is not a directory: ${resolvedPath}`);
          process.exit(1);
        }
      } catch {
        console.error(`Error: Directory does not exist: ${resolvedPath}`);
        process.exit(1);
      }

      const port = parseInt(options.port, 10);
      const shouldOpen = options.open;

      const app = createApp(resolvedPath);

      const server = app.listen(port, '127.0.0.1', () => {
        const url = `http://localhost:${port}`;
        console.error(`fdsx-ui server running at ${url}`);
        if (shouldOpen) {
          void open(url);
        }
      });

      const shutdown = (): void => {
        console.error('\nShutting down...');
        server.close(() => {
          process.exit(0);
        });
      };

      process.on('SIGINT', shutdown);
      process.on('SIGTERM', shutdown);
    });

  await program.parseAsync();
}

void main();
