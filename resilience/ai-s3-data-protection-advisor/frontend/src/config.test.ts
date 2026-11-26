import { afterEach, describe, expect, it, vi } from 'vitest';
import { ConfigError, getConfig, loadConfig, resetConfig } from './config';

const valid = { region: 'eu-west-1', userPoolId: 'eu-west-1_abc', userPoolClientId: 'client123' };

function mockFetch(body: unknown, ok = true, status = 200) {
  const fn = vi.fn().mockResolvedValue({ ok, status, json: async () => body });
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => {
  resetConfig();
  vi.unstubAllGlobals();
});

describe('loadConfig', () => {
  it('returns the values and fetches /config.json once', async () => {
    const fn = mockFetch(valid);
    const expected = { ...valid, multiAccount: false };
    expect(await loadConfig()).toEqual(expected);
    expect(await loadConfig()).toEqual(expected);
    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn.mock.calls[0][0]).toBe('/config.json');
    expect(getConfig()).toEqual(expected);
  });

  it('reads multiAccount only when it is the boolean true', async () => {
    mockFetch({ ...valid, multiAccount: true });
    expect((await loadConfig()).multiAccount).toBe(true);
    resetConfig();
    mockFetch({ ...valid, multiAccount: 'true' });
    expect((await loadConfig()).multiAccount).toBe(false);
  });

  it('throws a ConfigError when a field is missing', async () => {
    mockFetch({ region: 'eu-west-1', userPoolId: 'x' });
    await expect(loadConfig()).rejects.toBeInstanceOf(ConfigError);
  });

  it('throws a ConfigError when a field is an empty string', async () => {
    mockFetch({ ...valid, userPoolId: '' });
    await expect(loadConfig()).rejects.toBeInstanceOf(ConfigError);
  });

  it('throws a ConfigError on a failed response or a network error', async () => {
    mockFetch({}, false, 404);
    await expect(loadConfig()).rejects.toBeInstanceOf(ConfigError);
    resetConfig();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    await expect(loadConfig()).rejects.toBeInstanceOf(ConfigError);
  });

  it('getConfig throws before the configuration is loaded', () => {
    expect(() => getConfig()).toThrow(ConfigError);
  });
});
