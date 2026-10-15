/** Disposable socket-only PostgreSQL 17, no DSN/production/provider inputs. */
import { existsSync, mkdtempSync, readdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
if (process.argv.slice(2).join(' ') !== '--local-only') throw Error('explicit --local-only required');
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const bin='/opt/homebrew/opt/postgresql@17/bin/';
if (!existsSync(bin+'initdb')) throw Error('local PostgreSQL 17 required');
const directory=mkdtempSync('/private/tmp/coineasy-preserved-proof-');
const python=process.env.EXACT_CLAIM_LOCAL_PYTHON || 'python3';
const port='65438';
const env={PATH:bin+':'+process.env.PATH,LANG:'C',TZ:'UTC',PGHOST:directory,PGPORT:port,
  PGUSER:'postgres',PGDATABASE:'coineasy_preserved_test',PGPASSFILE:'/dev/null',PGCONNECT_TIMEOUT:'3',
  PYTHONPATH:root,PYTHONDONTWRITEBYTECODE:'1',EXACT_CLAIM_PSQL:bin+'psql',
  EXACT_CLAIM_DISPOSABLE_DATABASE:'coineasy_preserved_test'};
let started=false;
function run(command,args,extra={}) {
  const result=spawnSync(command,args,{cwd:root,env,encoding:'utf8',timeout:180000,maxBuffer:4*1024*1024,...extra});
  if(result.error || result.status!==0) throw Error(`local preserved SQL proof failed: ${String(result.stderr??'').slice(-6000)}`);
  return result.stdout.trim();
}
const pg=['-X','--no-password','-q','-v','ON_ERROR_STOP=1'];
function file(path){run(bin+'psql',[...pg,'-f',path]);}
try {
  run(bin+'initdb',['-D',directory,'-U','postgres','--auth=trust','--no-locale','-E','UTF8']);
  run(bin+'pg_ctl',['-D',directory,'-l',directory+'/server.log','-o',`-h '' -k ${directory} -p ${port}`,'-w','start']);started=true;
  run(bin+'createdb',['--no-password',env.PGDATABASE]);
  file('supabase/tests/bootstrap_local_postgres.sql');
  run(bin+'psql',[...pg,'-c',"create function auth.role() returns text language sql stable as $$select current_setting('request.jwt.claim.role',true)$$;"]);
  const migrations=readdirSync(resolve(root,'supabase/migrations')).filter(name=>name.endsWith('.sql') && name<'20260901120000').sort();
  for(const name of migrations) file('supabase/migrations/'+name);
  // Mirror CI ordering, retaining synthetic prior data for invariance checks.
  console.log(run(python,['ops/ybo-server-trial/exact_claim_local_unittest.py','--local-only']));
  console.log(run(python,['ops/ybo-preserved-recovery/recovery_local_unittest.py','--local-only']));
  console.log(JSON.stringify({preservedBabylonSqlPassed:true,postgresVersion:run(bin+'psql',[...pg,'-Atc','show server_version_num']),
    productionCalls:0,providerCalls:0,telegramCalls:0}));
} finally {
  if(started)run(bin+'pg_ctl',['-D',directory,'-m','fast','-w','stop']);
  console.log(JSON.stringify({disposableServerStopped:started,socketRemoved:!existsSync(directory+'/.s.PGSQL.'+port)}));
}
