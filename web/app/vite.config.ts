import {defineConfig} from 'vitest/config';
import {viteSingleFile} from 'vite-plugin-singlefile';
import {fileURLToPath} from 'node:url';
export default defineConfig(({mode}) => ({server:{proxy:{"/api":"http://127.0.0.1:8787"}},test:{minWorkers:1,maxWorkers:1},base:'./',publicDir:mode==='single'?false:'../data',plugins:mode==='single'?[viteSingleFile()]:[],build:{outDir:fileURLToPath(new URL(mode==='single'?'../../dist/single':'../../dist/app',import.meta.url)),emptyOutDir:true}}));
