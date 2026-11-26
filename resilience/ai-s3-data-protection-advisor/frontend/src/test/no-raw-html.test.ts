import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const root = resolve(__dirname, '..');
const self = resolve(__filename);

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? files(p) : [p];
  });
}

describe('raw HTML', () => {
  it('is never injected: no dangerouslySetInnerHTML or innerHTML under src/', () => {
    const offenders = files(root)
      .filter((f) => resolve(f) !== self)
      .filter((f) => /dangerouslySetInnerHTML|innerHTML/.test(readFileSync(f, 'utf8')));
    expect(offenders).toEqual([]);
  });
});
