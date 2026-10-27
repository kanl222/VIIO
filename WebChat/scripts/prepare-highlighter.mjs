import { copyFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { codeLanguages } from '../public/highlight-languages.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.join(root, 'node_modules', '@highlightjs', 'cdn-assets');
const target = path.join(root, 'public', 'vendor', 'highlight');
await mkdir(path.join(target, 'languages'), { recursive: true });
await copyFile(path.join(source, 'es', 'core.min.js'), path.join(target, 'core.min.js'));
await copyFile(path.join(source, 'LICENSE'), path.join(target, 'LICENSE'));
for (const language of codeLanguages) {
  await copyFile(path.join(source, 'es', 'languages', `${language}.min.js`),
    path.join(target, 'languages', `${language}.min.js`));
}
