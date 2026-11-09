import {defineConfig} from 'vitest/config';
import {viteSingleFile} from 'vite-plugin-singlefile';
import {fileURLToPath} from 'node:url';
import {readFileSync} from 'node:fs';
const samplePath = fileURLToPath(new URL('./public/sample-events.jsonl', import.meta.url));
export default defineConfig(({mode}) => ({
 test:{minWorkers:1,maxWorkers:1}, base:'./',
 publicDir:mode==='single'?false:'../data',
 plugins:[...(mode==='single'?[viteSingleFile()]:[]), {
  name:'sample-activity',
  configureServer(server) {
   server.middlewares.use((req,res,next) => {
    if(req.url?.split('?')[0] !== '/sample-events.jsonl') return next();
    res.setHeader('Content-Type','application/x-ndjson; charset=utf-8');
    res.end(readFileSync(samplePath));
   });
  },
  generateBundle() {this.emitFile({type:'asset',fileName:'sample-events.jsonl',source:readFileSync(samplePath)});}
 }],
 build:{outDir:fileURLToPath(new URL(mode==='single'?'../../dist/single':'./dist',import.meta.url)),emptyOutDir:true}
}));
