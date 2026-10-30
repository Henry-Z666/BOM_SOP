// Compatibility entry point. New installations should run build_component_sop.py directly.
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const script = fileURLToPath(new URL('./build_component_sop.py', import.meta.url));
const result = spawnSync(process.env.CREO_SOP_PYTHON || 'python',
  [script, ...process.argv.slice(2)], {stdio: 'inherit'});
if (result.error) console.error(result.error.message);
process.exit(result.status ?? 1);
