#!/usr/bin/env node
import {pathToFileURL} from 'node:url';
import {InspectError, validateRequest} from './lib/telegram-resolution-inspect.mjs';
import {readIntentFile, validationSummary, executeInspectOnce} from './lib/telegram-resolution-inspect-io.mjs';

const HELP = `Usage: node scripts/inspect-telegram-delivery-unknown.mjs --request FILE [--validate-only]

Default: validate a supplied exact request; no key read, signature, or network.
Future separately approved execution adds all of:
  --inspect-once --authorization FILE --signing-key-fd N --publishable-key-fd N
  --attempt-ledger-dir ABSOLUTE_DIRECTORY

Only already-trusted ES256 signing keys and sb_publishable_ API keys are supported.
No approve, resolve, key import, token output, provider send, or retry command exists.
`;

export function parseArguments(argv) {
  if (argv.length === 1 && argv[0] === '--help') return {help: true};
  const switches = new Set(['--validate-only', '--inspect-once']);
  const values = new Set(['--request', '--authorization', '--signing-key-fd', '--publishable-key-fd', '--attempt-ledger-dir']);
  const parsed = {};
  for (let i = 0; i < argv.length; i++) {
    const key = argv[i];
    if ((!switches.has(key) && !values.has(key)) || Object.hasOwn(parsed, key)) throw new InspectError('arguments_invalid');
    if (switches.has(key)) parsed[key] = true;
    else {
      if (!argv[i + 1] || argv[i + 1].startsWith('--')) throw new InspectError('arguments_invalid');
      parsed[key] = argv[++i];
    }
  }
  if (!parsed['--request'] || (parsed['--inspect-once'] && parsed['--validate-only'])) throw new InspectError('arguments_invalid');
  const live = ['--authorization', '--signing-key-fd', '--publishable-key-fd', '--attempt-ledger-dir'];
  if (parsed['--inspect-once']) {
    if (!live.every(k => parsed[k])) throw new InspectError('inspect_arguments_required');
    for (const key of ['--signing-key-fd', '--publishable-key-fd']) {
      if (!/^[1-9][0-9]*$/.test(parsed[key]) || !Number.isSafeInteger(Number(parsed[key])) || Number(parsed[key]) < 3) throw new InspectError('secret_descriptors_invalid');
    }
  } else if (live.some(k => parsed[k])) throw new InspectError('validate_only_rejects_credentials');
  return parsed;
}

export async function main(argv = process.argv.slice(2), deps = {}) {
  const output = deps.output ?? (value => process.stdout.write(value));
  try {
    const args = parseArguments(argv);
    if (args.help) { output(HELP); return 0; }
    const read = deps.readIntent ?? readIntentFile;
    const request = read(args['--request']);
    const validated = validateRequest(request, (deps.now ?? (() => new Date()))());
    if (!args['--inspect-once']) { output(JSON.stringify(validationSummary(validated)) + '\n'); return 0; }
    const authorization = read(args['--authorization']);
    const result = await executeInspectOnce({request, authorization,
      signingKeyFd: Number(args['--signing-key-fd']), publishableKeyFd: Number(args['--publishable-key-fd']),
      attemptLedgerDir: args['--attempt-ledger-dir']}, deps);
    output(JSON.stringify(result) + '\n');
    return 0;
  } catch (error) {
    output(JSON.stringify({ok: false, error: error instanceof InspectError ? error.code : 'operator_input_failed',
      credential_issued: error?.credentialIssued === true,
      database_calls: error?.rpcAttempted === true ? 1 : 0,
      provider_calls: 0, automatic_retry: false}) + '\n');
    return 2;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await main();
}
